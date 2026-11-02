/* Permissive policy variant (ARCHITECTURE §7): every capability and object rule allows and confirmations
 * auto-answer yes. Baseline for evals only; the banner and every audit line carry the label. Physical limits,
 * the interlock and the E-stop latch stay (INV-13 is below the AI, not part of the authority model). */
#ifndef RING3_POLICY_VARIANT_H
#define RING3_POLICY_VARIANT_H
#define POLICY_PERMISSIVE 1
#define POLICY_VARIANT_NAME "PERMISSIVE (EVAL ONLY)"
#endif
