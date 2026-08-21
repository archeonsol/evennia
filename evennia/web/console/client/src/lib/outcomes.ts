/* The five outcomes must not render alike.
 *
 * A `partial` and a `recovery_required` both mean "it wrote, then faulted", and
 * the difference between them is whether a person has to go and fix something.
 * Collapsing them to one colour loses exactly the distinction an operator reads
 * the audit trail to find.
 */

export const OUTCOME_STATE: Record<string, string> = {
  success: "ok",
  conflict: "off",
  partial: "fail",
  recovery_required: "fail",
  indeterminate: "attn",
};
