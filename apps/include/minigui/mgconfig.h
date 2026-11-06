/* Minimal mgconfig.h for compiling against libminigui_ths-3.2.so.0 */
/* "ths" = threads runtime mode */

#define MINIGUI_MAJOR_VERSION 3
#define MINIGUI_MINOR_VERSION 2
#define MINIGUI_MICRO_VERSION 0

#define PACKAGE "libminigui"
#define PACKAGE_BUGREPORT ""
#define PACKAGE_NAME "libminigui"
#define PACKAGE_STRING "libminigui 3.2.0"
#define PACKAGE_TARNAME "libminigui"
#define PACKAGE_VERSION "3.2.0"
#define VERSION "3.2.0"

/* Runtime mode: MiniGUI-Threads */
#define _MGRM_THREADS 1

/* Target OS */
#define __LINUX__ 1

/* Have standard headers */
#define HAVE_STDLIB_H 1
#define HAVE_STRING_H 1
#define HAVE_STDIO_H 1
#define HAVE_UNISTD_H 1
#define HAVE_TIME 1

/* Image support */
#define _MGIMAGE_PNG 1
#define _MGIMAGE_JPG 1
#define _MGIMAGE_BMP 1

/* Font support */
#define _MGFONT_TTF 1
#define _MGFONT_FT2 1
#define _MGFONT_RBF 1
#define _MGFONT_SEF 1

/* GAL engines */
#define _MGGAL_SHADOW 1
#define _MGGAL_DRMCON 1
#define _MGGAL_FBCON 1

/* IAL engines */
#define _MGIAL_CONSOLE 1

/* Control support */
#define _MGCTRL_STATIC 1
#define _MGCTRL_BUTTON 1

/* Misc features */
#define _MGHAVE_CLIPBOARD 1
#define _MGHAVE_CURSOR 1
#define _MGHAVE_MENU 1
#define _MGHAVE_FIXED_MATH 1
