/* The one vocabulary for decisions shown to people (T-08 correction 4): the LCD panel (kernel/drivers/device.c)
 * and the VGA status screen (kernel/ui/screen.c) both take their verdict words and reason phrases from here.
 * Pure, freestanding C (no kernel headers beyond the policy's enums), built for the kernel and the host.
 * INV-11: no banned words. */
#ifndef RING3_UI_WORDS_H
#define RING3_UI_WORDS_H
#include "policy.h"
#include <stdint.h>

/* ALLOW, DENIED, ASK, CONFIRMED, DECLINED, AUTO HALT, BOARD BOOT; 0 for a decision people are not shown
 * (FRAME_DROP, LIMIT, PROC_FAULT, NONE: the screen's executor line shows a kill). */
const char *ui_verdict_word(uint8_t code);
/* 1 for a verdict that refuses something (DENIED, DECLINED): the panel beeps, the screen uses the alarm colours. */
int ui_verdict_adverse(uint8_t code);
/* Short reason phrase (<= 16 chars, fits the LCD row): "hand too close", "fan max 80%", ... */
const char *ui_reason_words(uint8_t reason);
/* The second panel row for a decision: "within limits" for a plain ALLOW, "operator: yes" for CONFIRMED,
 * otherwise ui_reason_words(reason). */
const char *ui_detail_words(uint8_t code, uint8_t reason);
#endif
