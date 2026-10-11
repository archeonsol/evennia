// What the new-request form says.
//
// The words belong to the game (its narratives), and the client asks for them
// (`tickets:ticket_form`) so a change of wording is never a new client release. These
// are the words it shows until the game has answered, and when the game is an older
// one that does not know the request. Keep them the same as the game's.

export type RequestKind = "request" | "bug" | "report" | "puppet";

export interface FormKind {
  kind: RequestKind;
  name: string;
  hint: string;
}

export interface TicketForm {
  /** The question above the picker. */
  pick: string;
  kinds: FormKind[];
  summary: string;
  details: string;
  npc: string;
  said: string;
  goal: string;
  /** The label of the field where a player leaves a way to reach their character. */
  contact: string;
  category: string;
  categories: string[];
  severity: string;
  severities: { key: string; text: string }[];
  /** How to choose a severity, shown with the choices. */
  severity_advice: string;
  /** A line under the form of a kind. */
  notes: Record<string, string>;
}

/** What each priority means to staff, so High and Urgent are used the same way. */
export interface PriorityGuide {
  value: number;
  word: string;
  hint: string;
}

export const DEFAULT_FORM: TicketForm = {
  pick: "What do you need?",
  kinds: [
    { kind: "request", name: "Ask staff a question", hint: "Anything you need staff to look at." },
    { kind: "bug", name: "Report a bug", hint: "Something in the game is broken." },
    { kind: "report", name: "Report another player", hint: "Report a player to senior staff for investigation." },
    {
      kind: "puppet",
      name: "Ask for an NPC to be puppeted",
      hint: "After requesting, you can leave and go on with your RP while you wait.",
    },
  ],
  summary: "Brief summary",
  details: "Details",
  npc: "Which NPC",
  said: "What has happened so far",
  goal: "What you want from the scene",
  contact: "Contact information",
  category: "What kind of problem",
  categories: ["Movement", "Combat", "Roleplay", "NPCs", "Vehicles", "Other"],
  severity: "How bad",
  severities: [
    { key: "Trivial", text: "Trivial: a typo or a small glitch" },
    { key: "Minor", text: "Minor: annoying, but easy to get around" },
    { key: "Moderate", text: "Moderate: broken, with no way around it" },
    { key: "Severe", text: "Severe: a major failure, or a crash" },
    { key: "Critical", text: "Critical: the game stops, or an exploit" },
  ],
  severity_advice: "It helps us sort bugs.",
  notes: {
    request: "Staff answer as they can.",
    report: "Senior staff investigate it. The player you name is not told.",
    puppet: "You can leave and wait for a reply. Leave your character's contact information so staff can reach you.",
  },
};

export const DEFAULT_PRIORITIES: PriorityGuide[] = [
  { value: 0, word: "Low", hint: "Can wait. Nobody is blocked." },
  { value: 1, word: "Normal", hint: "An ordinary ticket." },
  { value: 2, word: "High", hint: "Someone cannot play, or a bug has no way around it." },
  { value: 3, word: "Urgent", hint: "An exploit, or harm happening now. Only staff set this." },
];

/** The game's form laid over the defaults, so a missing word is never a blank label. */
export function mergeForm(answer: Partial<TicketForm> | null | undefined): TicketForm {
  if (!answer || typeof answer !== "object") return DEFAULT_FORM;
  const kinds = Array.isArray(answer.kinds) && answer.kinds.length ? answer.kinds : DEFAULT_FORM.kinds;
  const severities =
    Array.isArray(answer.severities) && answer.severities.length ? answer.severities : DEFAULT_FORM.severities;
  const categories =
    Array.isArray(answer.categories) && answer.categories.length ? answer.categories : DEFAULT_FORM.categories;
  return {
    ...DEFAULT_FORM,
    ...Object.fromEntries(Object.entries(answer).filter(([, v]) => v !== undefined && v !== null && v !== "")),
    kinds,
    severities,
    categories,
    notes: { ...DEFAULT_FORM.notes, ...(answer.notes ?? {}) },
  } as TicketForm;
}
