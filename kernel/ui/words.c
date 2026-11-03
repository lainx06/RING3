/* See words.h. Moved here from the LCD driver (T-04) so the panel and the screen share one table. */
#include "words.h"

const char *ui_verdict_word(uint8_t code)
{
    switch (code) {
    case DEC_ALLOW:           return "ALLOW";
    case DEC_CONFIRM_YES:     return "CONFIRMED";
    case DEC_CONFIRM_NO:      return "DECLINED";
    case DEC_CONFIRM_PENDING: return "ASK";
    case DEC_DENY_CAP:
    case DEC_DENY_RULE:
    case DEC_DENY_ARG:
    case DEC_DENY_FAULT:
    case DEC_DENY_LIMIT:
    case DEC_DENY_INTERLOCK:
    case DEC_DENY_STALE:
    case DEC_DENY_ESTOP:      return "DENIED";
    case DEC_AUTO_HALT:       return "AUTO HALT";
    case DEC_BOARD_BOOT:      return "BOARD BOOT";
    default:                  return 0;
    }
}

int ui_verdict_adverse(uint8_t code)
{
    switch (code) {
    case DEC_CONFIRM_NO:
    case DEC_DENY_CAP:
    case DEC_DENY_RULE:
    case DEC_DENY_ARG:
    case DEC_DENY_FAULT:
    case DEC_DENY_LIMIT:
    case DEC_DENY_INTERLOCK:
    case DEC_DENY_STALE:
    case DEC_DENY_ESTOP:      return 1;
    default:                  return 0;
    }
}

const char *ui_reason_words(uint8_t reason)
{
    switch (reason) {
    case R_PRESENCE:     return "hand too close";
    case R_NO_ECHO:      return "sensor: no echo";
    case R_STALE:        return "stale telemetry";
    case R_NO_TELEMETRY: return "no telemetry";
    case R_ESTOP:        return "E-STOP latched";
    case R_ESTOP_BUTTON: return "button pressed";
    case R_FAN_RANGE:    return "fan max 80%";
    case R_FAN_STEP:     return "fan step > 20";
    case R_FAN_RATE:     return "fan: too soon";
    case R_SERVO_RANGE:  return "servo 30-150";
    case R_SERVO_STEP:   return "servo step > 30";
    case R_SERVO_RATE:   return "servo: too soon";
    case R_ARG:          return "bad value";
    case R_FIRST_FAN:    return "first fan: ask";
    case R_TAINT:        return "tainted: ask";
    case R_ANSWER:       return "operator said no";
    case R_TIMEOUT:      return "no answer";
    case R_CAP:          return "no capability";
    case R_PENDING:      return "prompt pending";
    case R_NO_REQUEST:   return "no request";
    case R_BOARD_BOOT:   return "fresh boot";
    case R_ESTOP_RESET:  return "console reset";
    case R_HALT:         return "halt";
    default:             return "policy";
    }
}

const char *ui_detail_words(uint8_t code, uint8_t reason)
{
    if (code == DEC_ALLOW && reason == R_NONE)
        return "within limits";
    if (code == DEC_CONFIRM_YES)
        return "operator: yes";
    return ui_reason_words(reason);
}
