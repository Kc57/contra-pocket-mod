/*
 * Game Launcher for My Arcade Contra Pocket Player
 *
 * Dynamically scans /usr/lib/libretro/games/fzip/ for .zip ROMs.
 * Optional names.cfg provides human-readable display names.
 *
 * UP/DOWN to navigate (wraps around, scrolls),
 * A or START to launch. Selected game shown in green.
 * Launches RetroArch via fork/exec, returns to menu on exit.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/wait.h>
#include <dirent.h>

#include <minigui/common.h>
#include <minigui/minigui.h>
#include <minigui/gdi.h>
#include <minigui/window.h>

#define SCREEN_W 240
#define SCREEN_H 320

/* Button scancodes (verified on hardware) */
#define SC_UP    103
#define SC_DOWN  108
#define SC_START 28
#define SC_A     30
#define SC_B     48
#define SC_COIN  54    /* Triggers multiplayer */

/* ---------- Game list ---------- */

#define MAX_GAMES   128
#define GAME_NAME_LEN 128
#define MAX_PATH_LEN 256

#define ROM_DIR      "/usr/lib/libretro/games/fzip/"
#define CORE_PATH    "/usr/lib/libretro/fbalpha_libretro.so"
#define CONFIG_PATH  "/usr/lib/libretro/games/zip.cfg"
#define CONFIG_MP    "/usr/lib/libretro/games/zip_p.cfg"
#define NAMES_FILE   "/usr/lib/libretro/games/fzip/names.cfg"

/* Multiplayer (CO/VS cable netplay) */
#define MP_IP_INIT       "192.168.3.1"
#define MP_IP_HOST       "192.168.3.2"
#define MP_IP_CLIENT     "192.168.3.3"

typedef struct {
    char name[GAME_NAME_LEN];
    char rom_path[MAX_PATH_LEN];
} game_t;

static game_t games[MAX_GAMES];
static int num_games = 0;
static int selected = 0;
static int scroll_offset = 0;
static int last_exit_code = 0;

/* Multiplayer state */
typedef enum {
    MP_IDLE,
    MP_CONFIGURING,
    MP_SEARCHING,
    MP_HOSTING,
    MP_CONNECTING,
    MP_LAUNCHING,
    MP_ERROR,
} mp_state_t;

typedef enum {
    MP_ROLE_NONE,
    MP_ROLE_HOST,
    MP_ROLE_CLIENT,
} mp_role_t;

static mp_state_t mp_state = MP_IDLE;
static mp_role_t  mp_role  = MP_ROLE_NONE;
static char       mp_status_msg[128] = "";
static pid_t      mp_ping_pid = -1;

#define TIMER_MP       1
#define TIMER_MP_TICK  50  /* 500ms between ticks (MiniGUI timer unit = 10ms) */
#define TIMER_FLUSH    2
#define TIMER_FLUSH_TICK 30 /* 300ms input ignore window after game exit */

static int input_ignore = 0;

/* ---------- Name mapping ---------- */

#define GAME_NAME_LENS 256

typedef struct {
    char stem[GAME_NAME_LEN];
    char display[GAME_NAME_LEN];
} name_entry_t;

static name_entry_t name_map[GAME_NAME_LENS];
static int num_names = 0;

static void load_names(void)
{
    FILE *fp = fopen(NAMES_FILE, "r");
    if (!fp)
        return;

    char line[512];
    while (fgets(line, sizeof(line), fp) && num_names < GAME_NAME_LENS) {
        /* Strip newline */
        char *nl = strchr(line, '\n');
        if (nl) *nl = '\0';
        char *cr = strchr(line, '\r');
        if (cr) *cr = '\0';

        /* Skip blank lines and comments */
        if (line[0] == '\0' || line[0] == '#')
            continue;

        char *eq = strchr(line, '=');
        if (!eq)
            continue;

        *eq = '\0';
        const char *stem = line;
        const char *display = eq + 1;

        snprintf(name_map[num_names].stem, GAME_NAME_LEN, "%s", stem);
        snprintf(name_map[num_names].display, GAME_NAME_LEN, "%s", display);
        num_names++;
    }

    fclose(fp);
}

static const char *lookup_name(const char *stem)
{
    for (int i = 0; i < num_names; i++) {
        if (strcmp(name_map[i].stem, stem) == 0)
            return name_map[i].display;
    }
    return NULL;
}

/* ---------- ROM scanning ---------- */

static int game_cmp(const void *a, const void *b)
{
    return strcasecmp(((const game_t *)a)->name, ((const game_t *)b)->name);
}

static void scan_roms(void)
{
    DIR *dir = opendir(ROM_DIR);
    if (!dir)
        return;

    struct dirent *ent;
    while ((ent = readdir(dir)) != NULL && num_games < MAX_GAMES) {
        const char *fname = ent->d_name;
        size_t len = strlen(fname);

        /* Must end in .zip */
        if (len < 5 || strcasecmp(fname + len - 4, ".zip") != 0)
            continue;

        /* Build rom path */
        snprintf(games[num_games].rom_path, MAX_PATH_LEN, "%s%s", ROM_DIR, fname);

        /* Extract stem (filename without .zip) */
        char stem[GAME_NAME_LEN];
        size_t stem_len = len - 4;
        if (stem_len >= GAME_NAME_LEN) stem_len = GAME_NAME_LEN - 1;
        memcpy(stem, fname, stem_len);
        stem[stem_len] = '\0';

        /* Look up display name, fall back to stem */
        const char *display = lookup_name(stem);
        if (display)
            snprintf(games[num_games].name, GAME_NAME_LEN, "%s", display);
        else
            snprintf(games[num_games].name, GAME_NAME_LEN, "%s", stem);

        num_games++;
    }

    closedir(dir);

    /* Sort alphabetically by display name */
    if (num_games > 0)
        qsort(games, num_games, sizeof(game_t), game_cmp);
}

/* ---------- Multiplayer helpers ---------- */

static int setup_network_init(void)
{
    /* Cycle eth0 down/up to clear stale state from previous sessions */
    system("/sbin/ifconfig eth0 down");
    return system("/sbin/ifconfig eth0 " MP_IP_INIT " netmask 255.255.255.0 up");
}

static int setup_network_host(void)
{
    return system("/sbin/ifconfig eth0 " MP_IP_HOST " netmask 255.255.255.0");
}

static int setup_network_client(void)
{
    return system("/sbin/ifconfig eth0 " MP_IP_CLIENT " netmask 255.255.255.0");
}

/* ---------- Launch ---------- */

static void launch_game(const game_t *game, mp_role_t role, HWND hWnd)
{
    pid_t pid = fork();
    if (pid < 0)
        return;

    if (pid == 0) {
        setsid();
        for (int fd = 3; fd < 1024; fd++)
            close(fd);

        int log_fd = open("/userdata/retroarch.log",
                          O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (log_fd >= 0) {
            dup2(log_fd, STDERR_FILENO);
            dup2(log_fd, STDOUT_FILENO);
            close(log_fd);
        }

        setenv("XDG_CONFIG_HOME", "/data", 1);
        setenv("XDG_RUNTIME_DIR", "/data", 1);
        setenv("LC_ALL", "zh_CN.utf8", 1);

        const char *cfg = (role != MP_ROLE_NONE) ? CONFIG_MP : CONFIG_PATH;

        switch (role) {
        case MP_ROLE_HOST:
            execl("/usr/bin/retroarch", "retroarch",
                  "-y", "16",
                  "-c", cfg,
                  "-L", CORE_PATH,
                  "-H",
                  game->rom_path,
                  (char *)NULL);
            break;
        case MP_ROLE_CLIENT:
            execl("/usr/bin/retroarch", "retroarch",
                  "-y", "16",
                  "-c", cfg,
                  "-L", CORE_PATH,
                  "-C", MP_IP_HOST,
                  game->rom_path,
                  (char *)NULL);
            break;
        default:
            execl("/usr/bin/retroarch", "retroarch",
                  "-y", "12",
                  "-c", cfg,
                  "-L", CORE_PATH,
                  game->rom_path,
                  (char *)NULL);
            break;
        }

        dprintf(STDERR_FILENO, "execl failed: errno=%d\n", errno);
        _exit(127);
    }

    int status;
    waitpid(pid, &status, 0);

    if (WIFEXITED(status))
        last_exit_code = WEXITSTATUS(status);
    else if (WIFSIGNALED(status))
        last_exit_code = -(WTERMSIG(status));
    else
        last_exit_code = 0;

    /* Ignore stale input events that queued during gameplay.
     * Events are still in the kernel evdev buffer at this point --
     * MiniGUI won't read them until GetMessage runs in the main loop.
     * Set a flag + timer so the KEYDOWN handler discards them. */
    input_ignore = 1;
    SetTimer(hWnd, TIMER_FLUSH, TIMER_FLUSH_TICK);
}

/* ---------- Drawing ---------- */

#define TITLE_Y     20
#define LIST_Y      60
#define LINE_H      26
#define LIST_X      16
#define TEXT_INDENT  28
#define FOOTER_H    40

/* How many items fit in the visible area */
#define VISIBLE_COUNT ((SCREEN_H - LIST_Y - FOOTER_H) / LINE_H)

static void paint_menu(HWND hWnd)
{
    HDC hdc = BeginPaint(hWnd);

    /* Black background */
    SetBrushColor(hdc, RGB2Pixel(hdc, 0x00, 0x00, 0x00));
    FillBox(hdc, 0, 0, SCREEN_W, SCREEN_H);
    SetBkMode(hdc, BM_TRANSPARENT);

    /* Title */
    SetTextColor(hdc, RGB2Pixel(hdc, 0x00, 0xAA, 0xFF));
    TextOut(hdc, 10, TITLE_Y, "SELECT GAME");

    /* Divider under title */
    SetPenColor(hdc, RGB2Pixel(hdc, 0x33, 0x33, 0x33));
    MoveTo(hdc, 10, TITLE_Y + 22);
    LineTo(hdc, 230, TITLE_Y + 22);

    if (num_games == 0) {
        SetTextColor(hdc, RGB2Pixel(hdc, 0xAA, 0xAA, 0xAA));
        TextOut(hdc, TEXT_INDENT, LIST_Y, "No games found");
    } else {
        /* Scroll indicator: up arrow */
        if (scroll_offset > 0) {
            SetTextColor(hdc, RGB2Pixel(hdc, 0x66, 0x66, 0x66));
            TextOut(hdc, SCREEN_W / 2 - 4, LIST_Y - 16, "...");
        }

        /* Game list (visible window) */
        int visible = VISIBLE_COUNT;
        if (visible > num_games)
            visible = num_games;

        for (int i = 0; i < visible; i++) {
            int gi = scroll_offset + i;  /* game index */
            if (gi >= num_games)
                break;

            int y = LIST_Y + i * LINE_H;

            if (gi == selected) {
                /* Selected: green text with arrow indicator */
                SetTextColor(hdc, RGB2Pixel(hdc, 0x00, 0xFF, 0x00));
                TextOut(hdc, LIST_X, y, ">");
                TextOut(hdc, TEXT_INDENT, y, games[gi].name);
            } else {
                /* Normal: gray text */
                SetTextColor(hdc, RGB2Pixel(hdc, 0xAA, 0xAA, 0xAA));
                TextOut(hdc, TEXT_INDENT, y, games[gi].name);
            }
        }

        /* Scroll indicator: down arrow */
        if (scroll_offset + visible < num_games) {
            SetTextColor(hdc, RGB2Pixel(hdc, 0x66, 0x66, 0x66));
            TextOut(hdc, SCREEN_W / 2 - 4, LIST_Y + visible * LINE_H, "...");
        }
    }

    /* Hints at bottom */
    SetTextColor(hdc, RGB2Pixel(hdc, 0x44, 0x44, 0x44));
    TextOut(hdc, 10, SCREEN_H - 34, "UP/DOWN: Select");

    if (last_exit_code != 0) {
        char errbuf[48];
        if (last_exit_code > 0)
            snprintf(errbuf, sizeof(errbuf), "Exit: %d (see retroarch.log)", last_exit_code);
        else
            snprintf(errbuf, sizeof(errbuf), "Signal: %d", -last_exit_code);
        SetTextColor(hdc, RGB2Pixel(hdc, 0xFF, 0x44, 0x44));
        TextOut(hdc, 10, SCREEN_H - 18, errbuf);
    } else {
        TextOut(hdc, 10, SCREEN_H - 18, "A: Launch  COIN: 2P");
    }

    EndPaint(hWnd, hdc);
}

/* ---------- Scroll helpers ---------- */

static void adjust_scroll(void)
{
    int visible = VISIBLE_COUNT;
    if (visible > num_games)
        visible = num_games;

    /* Keep selected item in view */
    if (selected < scroll_offset)
        scroll_offset = selected;
    else if (selected >= scroll_offset + visible)
        scroll_offset = selected - visible + 1;
}

/* ---------- Multiplayer UI ---------- */

static void paint_mp_status(HWND hWnd)
{
    HDC hdc = BeginPaint(hWnd);

    SetBrushColor(hdc, RGB2Pixel(hdc, 0x00, 0x00, 0x00));
    FillBox(hdc, 0, 0, SCREEN_W, SCREEN_H);
    SetBkMode(hdc, BM_TRANSPARENT);

    /* Title */
    SetTextColor(hdc, RGB2Pixel(hdc, 0xFF, 0xAA, 0x00));
    TextOut(hdc, 10, TITLE_Y, "MULTIPLAYER");

    SetPenColor(hdc, RGB2Pixel(hdc, 0x33, 0x33, 0x33));
    MoveTo(hdc, 10, TITLE_Y + 22);
    LineTo(hdc, 230, TITLE_Y + 22);

    /* Game name */
    SetTextColor(hdc, RGB2Pixel(hdc, 0x00, 0xFF, 0x00));
    if (num_games > 0)
        TextOut(hdc, 10, 60, games[selected].name);

    /* Status message */
    if (mp_state == MP_ERROR)
        SetTextColor(hdc, RGB2Pixel(hdc, 0xFF, 0x44, 0x44));
    else if (mp_state == MP_HOSTING || mp_state == MP_CONNECTING)
        SetTextColor(hdc, RGB2Pixel(hdc, 0x00, 0xAA, 0xFF));
    else
        SetTextColor(hdc, RGB2Pixel(hdc, 0xAA, 0xAA, 0xAA));
    TextOut(hdc, 10, 100, mp_status_msg);

    /* Role indicator */
    if (mp_role != MP_ROLE_NONE) {
        SetTextColor(hdc, RGB2Pixel(hdc, 0x00, 0xAA, 0xFF));
        TextOut(hdc, 10, 140,
                mp_role == MP_ROLE_HOST ? "Role: HOST (Player 1)"
                                       : "Role: CLIENT (Player 2)");
    }

    /* Hint */
    SetTextColor(hdc, RGB2Pixel(hdc, 0x44, 0x44, 0x44));
    TextOut(hdc, 10, SCREEN_H - 18, "B: Cancel");

    EndPaint(hWnd, hdc);
}

/* ---------- Multiplayer state machine ---------- */

static void mp_cancel(HWND hWnd)
{
    KillTimer(hWnd, TIMER_MP);
    if (mp_ping_pid > 0) {
        kill(mp_ping_pid, SIGTERM);
        waitpid(mp_ping_pid, NULL, 0);
        mp_ping_pid = -1;
    }
    mp_state = MP_IDLE;
    mp_role = MP_ROLE_NONE;
}

static void mp_timer_tick(HWND hWnd)
{
    switch (mp_state) {
    case MP_CONFIGURING:
        snprintf(mp_status_msg, sizeof(mp_status_msg), "Configuring network...");
        InvalidateRect(hWnd, NULL, FALSE);

        if (setup_network_init() != 0) {
            snprintf(mp_status_msg, sizeof(mp_status_msg), "No cable connected");
            mp_state = MP_ERROR;
        } else {
            /* Start async ping to detect existing host */
            mp_ping_pid = fork();
            if (mp_ping_pid == 0) {
                execlp("ping", "ping", "-c", "1", "-W", "2",
                       MP_IP_HOST, (char *)NULL);
                _exit(1);
            }
            mp_state = MP_SEARCHING;
        }
        break;

    case MP_SEARCHING:
        snprintf(mp_status_msg, sizeof(mp_status_msg), "Searching for host...");
        InvalidateRect(hWnd, NULL, FALSE);

        if (mp_ping_pid > 0) {
            int status;
            pid_t result = waitpid(mp_ping_pid, &status, WNOHANG);
            if (result == 0)
                break;  /* Still pinging, wait for next tick */

            mp_ping_pid = -1;

            if (result > 0 && WIFEXITED(status) && WEXITSTATUS(status) == 0) {
                mp_role = MP_ROLE_CLIENT;
                mp_state = MP_CONNECTING;
            } else {
                mp_role = MP_ROLE_HOST;
                mp_state = MP_HOSTING;
            }
        }
        break;

    case MP_CONNECTING:
        snprintf(mp_status_msg, sizeof(mp_status_msg), "Connecting as Player 2...");
        InvalidateRect(hWnd, NULL, FALSE);

        if (setup_network_client() != 0) {
            snprintf(mp_status_msg, sizeof(mp_status_msg), "Client network failed");
            mp_state = MP_ERROR;
        } else {
            mp_state = MP_LAUNCHING;
        }
        break;

    case MP_HOSTING:
        snprintf(mp_status_msg, sizeof(mp_status_msg), "Hosting as Player 1...");
        InvalidateRect(hWnd, NULL, FALSE);

        if (setup_network_host() != 0) {
            snprintf(mp_status_msg, sizeof(mp_status_msg), "Host network failed");
            mp_state = MP_ERROR;
        } else {
            mp_state = MP_LAUNCHING;
        }
        break;

    case MP_LAUNCHING:
        KillTimer(hWnd, TIMER_MP);
        launch_game(&games[selected], mp_role, hWnd);
        mp_state = MP_IDLE;
        mp_role = MP_ROLE_NONE;
        InvalidateRect(hWnd, NULL, FALSE);
        break;

    case MP_ERROR:
        KillTimer(hWnd, TIMER_MP);
        InvalidateRect(hWnd, NULL, FALSE);
        break;

    default:
        break;
    }
}

/* ---------- Window proc ---------- */

static LRESULT LauncherWinProc(HWND hWnd, UINT message,
                                WPARAM wParam, LPARAM lParam)
{
    switch (message) {
    case MSG_PAINT:
        if (mp_state != MP_IDLE)
            paint_mp_status(hWnd);
        else
            paint_menu(hWnd);
        return 0;

    case MSG_TIMER:
        if ((int)wParam == TIMER_MP)
            mp_timer_tick(hWnd);
        else if ((int)wParam == TIMER_FLUSH) {
            input_ignore = 0;
            KillTimer(hWnd, TIMER_FLUSH);
        }
        return 0;

    case MSG_KEYDOWN: {
        int code = (int)wParam;

        /* Discard stale input from during gameplay */
        if (input_ignore)
            break;

        /* Multiplayer flow active — B cancels, ignore everything else */
        if (mp_state != MP_IDLE) {
            if (code == SC_B) {
                mp_cancel(hWnd);
                InvalidateRect(hWnd, NULL, FALSE);
            }
            break;
        }

        if (num_games == 0)
            break;

        switch (code) {
        case SC_UP:
            selected--;
            if (selected < 0)
                selected = num_games - 1;
            adjust_scroll();
            InvalidateRect(hWnd, NULL, FALSE);
            break;

        case SC_DOWN:
            selected++;
            if (selected >= num_games)
                selected = 0;
            adjust_scroll();
            InvalidateRect(hWnd, NULL, FALSE);
            break;

        case SC_A:
        case SC_START:
            launch_game(&games[selected], MP_ROLE_NONE, hWnd);
            InvalidateRect(hWnd, NULL, FALSE);
            break;

        case SC_COIN:
            mp_state = MP_CONFIGURING;
            SetTimer(hWnd, TIMER_MP, TIMER_MP_TICK);
            InvalidateRect(hWnd, NULL, FALSE);
            break;
        }
        break;
    }

    case MSG_CLOSE:
        DestroyMainWindow(hWnd);
        PostQuitMessage(hWnd);
        return 0;
    }

    return DefaultMainWinProc(hWnd, message, wParam, lParam);
}

/* ---------- Entry point ---------- */

int MiniGUIMain(int argc, const char *argv[])
{
    MSG Msg;
    HWND hMainWnd;
    MAINWINCREATE CreateInfo;

    /* Scan ROMs before creating window */
    load_names();
    scan_roms();

#ifdef _MGRM_PROCESSES
    JoinLayer(NAME_DEF_LAYER, "launcher", 0, 0);
#endif

    CreateInfo.dwStyle = WS_VISIBLE;
    CreateInfo.dwExStyle = WS_EX_NONE;
    CreateInfo.spCaption = "Game Launcher";
    CreateInfo.hMenu = 0;
    CreateInfo.hCursor = 0;
    CreateInfo.hIcon = 0;
    CreateInfo.MainWindowProc = LauncherWinProc;
    CreateInfo.lx = 0;
    CreateInfo.ty = 0;
    CreateInfo.rx = SCREEN_W;
    CreateInfo.by = SCREEN_H;
    CreateInfo.iBkColor = PIXEL_black;
    CreateInfo.dwAddData = 0;
    CreateInfo.hHosting = HWND_DESKTOP;

    hMainWnd = CreateMainWindow(&CreateInfo);
    if (hMainWnd == HWND_INVALID)
        return -1;

    ShowWindow(hMainWnd, SW_SHOWNORMAL);

    while (GetMessage(&Msg, hMainWnd)) {
        TranslateMessage(&Msg);
        DispatchMessage(&Msg);
    }

    MainWindowThreadCleanup(hMainWnd);
    return 0;
}
