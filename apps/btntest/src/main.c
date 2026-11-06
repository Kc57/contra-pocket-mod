/*
 * btntest — Simple button test for My Arcade Contra Pocket Player
 *
 * Shows which button was pressed/released with scancode and name.
 * Handles MSG_KEYDOWN and MSG_KEYUP. No timers, no logging.
 */

#include <stdio.h>
#include <string.h>

#include <minigui/common.h>
#include <minigui/minigui.h>
#include <minigui/gdi.h>
#include <minigui/window.h>

#define SCREEN_W 240
#define SCREEN_H 320

static const char *scancode_name(int code)
{
    switch (code) {
    case 103: return "Up";
    case 108: return "Down";
    case 105: return "Left";
    case 106: return "Right";
    case 28:  return "Start";
    case 30:  return "A";
    case 48:  return "B";
    case 54:  return "Coin";
    case 46:  return "Reset";
    case 114: return "Vol Dn";
    case 115: return "Vol Up";
    default:  return "???";
    }
}

/* Event history — each row stores the 3 columns separately */
#define HISTORY_SIZE 10

static struct {
    char state[8];   /* "KEY_DN" or "KEY_UP" */
    int  code;
    char name[10];
    int  used;
} history[HISTORY_SIZE];

static int history_count = 0;

static void push_history(const char *state, int code, const char *name)
{
    for (int i = HISTORY_SIZE - 1; i > 0; i--)
        history[i] = history[i - 1];

    snprintf(history[0].state, sizeof(history[0].state), "%s", state);
    history[0].code = code;
    snprintf(history[0].name, sizeof(history[0].name), "%s", name);
    history[0].used = 1;

    if (history_count < HISTORY_SIZE)
        history_count++;
}

/*
 * Layout (240px wide, 8px fixed font = 30 char positions):
 *
 *   State     Code  Button
 *   KEY_DN    103   Up
 *   KEY_UP    103   Up
 *
 *   x=8    STATE  (6 chars: KEY_DN/KEY_UP)
 *   x=80   CODE   (3 chars)
 *   x=128  BUTTON (up to 8 chars)
 */
#define COL_STATE  8
#define COL_CODE   80
#define COL_BUTTON 128

static void paint_screen(HWND hWnd)
{
    HDC hdc = BeginPaint(hWnd);

    SetBrushColor(hdc, RGB2Pixel(hdc, 0x00, 0x00, 0x00));
    FillBox(hdc, 0, 0, SCREEN_W, SCREEN_H);
    SetBkMode(hdc, BM_TRANSPARENT);

    int y = 10;

    /* Title */
    SetTextColor(hdc, RGB2Pixel(hdc, 0x00, 0xFF, 0x88));
    TextOut(hdc, 10, y, "Button Test");
    y += 26;

    /* Divider */
    SetPenColor(hdc, RGB2Pixel(hdc, 0x33, 0x33, 0x33));
    MoveTo(hdc, 10, y);
    LineTo(hdc, 230, y);
    y += 6;

    /* Column headers */
    SetTextColor(hdc, RGB2Pixel(hdc, 0x66, 0x66, 0x66));
    TextOut(hdc, COL_STATE,  y, "State");
    TextOut(hdc, COL_CODE,   y, "Code");
    TextOut(hdc, COL_BUTTON, y, "Button");
    y += 18;

    /* Divider */
    SetPenColor(hdc, RGB2Pixel(hdc, 0x33, 0x33, 0x33));
    MoveTo(hdc, 10, y);
    LineTo(hdc, 230, y);
    y += 6;

    /* History rows */
    for (int i = 0; i < history_count; i++) {
        int is_down = (strcmp(history[i].state, "KEY_DN") == 0);

        /* Fade older rows */
        int age = i;
        if (is_down) {
            int g = 0xFF - (age * 0x18);
            if (g < 0x44) g = 0x44;
            SetTextColor(hdc, RGB2Pixel(hdc, 0x00, g, 0x00));
        } else {
            int v = 0xAA - (age * 0x10);
            if (v < 0x33) v = 0x33;
            SetTextColor(hdc, RGB2Pixel(hdc, v, v, v));
        }

        /* State column */
        TextOut(hdc, COL_STATE, y, history[i].state);

        /* Code column */
        char code_str[8];
        snprintf(code_str, sizeof(code_str), "%d", history[i].code);
        TextOut(hdc, COL_CODE, y, code_str);

        /* Button column */
        TextOut(hdc, COL_BUTTON, y, history[i].name);

        y += 18;
    }

    /* Hint at bottom */
    SetTextColor(hdc, RGB2Pixel(hdc, 0x44, 0x44, 0x44));
    TextOut(hdc, 10, SCREEN_H - 20, "Press buttons to test");

    EndPaint(hWnd, hdc);
}

static LRESULT BtnTestProc(HWND hWnd, UINT message, WPARAM wParam, LPARAM lParam)
{
    switch (message) {
    case MSG_PAINT:
        paint_screen(hWnd);
        return 0;

    case MSG_KEYDOWN:
        push_history("KEY_DN", (int)wParam, scancode_name((int)wParam));
        InvalidateRect(hWnd, NULL, FALSE);
        break;

    case MSG_KEYUP:
        push_history("KEY_UP", (int)wParam, scancode_name((int)wParam));
        InvalidateRect(hWnd, NULL, FALSE);
        break;

    case MSG_CLOSE:
        DestroyMainWindow(hWnd);
        PostQuitMessage(hWnd);
        return 0;
    }

    return DefaultMainWinProc(hWnd, message, wParam, lParam);
}

int MiniGUIMain(int argc, const char *argv[])
{
    MSG Msg;
    HWND hMainWnd;
    MAINWINCREATE CreateInfo;

#ifdef _MGRM_PROCESSES
    JoinLayer(NAME_DEF_LAYER, "btntest", 0, 0);
#endif

    CreateInfo.dwStyle = WS_VISIBLE;
    CreateInfo.dwExStyle = WS_EX_NONE;
    CreateInfo.spCaption = "btntest";
    CreateInfo.hMenu = 0;
    CreateInfo.hCursor = 0;
    CreateInfo.hIcon = 0;
    CreateInfo.MainWindowProc = BtnTestProc;
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
