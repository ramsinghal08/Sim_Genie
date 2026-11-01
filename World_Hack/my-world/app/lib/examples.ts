export interface PromptExample {
  label: string;
  prompt: string;
}

// Quick-prompt chips. Generic wording ("the subject") so they work with any
// source; two are pointer-driven.
export const PROMPT_EXAMPLES: readonly PromptExample[] = [
  {
    label: "Replace with reference",
    prompt:
      "replace the subject in the video with the character from the reference image",
  },
  {
    label: "Spawn at pointer",
    prompt: "place the character from the reference image at the pointer",
  },
  {
    label: "Follow the drag",
    prompt: "make the subject follow the pointer as you drag",
  },
] as const;
