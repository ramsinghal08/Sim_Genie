import type { X2StateUpdateMessage } from "@reactor-models/x2";
import type { X2UiState } from "./types";

// Projects `state_update` snapshots into X2UiState, returning the previous
// object when nothing changed so React can bail out. The drag pointer is
// excluded (see PointerPanel). referenceAccepted comes from the separate ack
// message, so it is kept unless the snapshot reports no reference.
export function reduce(state: X2UiState, msg: X2StateUpdateMessage): X2UiState {
  const next: X2UiState = {
    generating: msg.generating,
    // Typed `unknown` on the wire; the model sends string/number or null.
    activePrompt: (msg.prompt as string | null) ?? null,
    outputWidth: (msg.width as number | null) ?? null,
    outputHeight: (msg.height as number | null) ?? null,
    hasReference: msg.has_reference_image,
    keepBacklog: msg.keep_backlog,
    referenceAccepted: msg.has_reference_image ? state.referenceAccepted : null,
  };
  const changed = (Object.keys(next) as (keyof X2UiState)[]).some(
    (k) => next[k] !== state[k],
  );
  return changed ? next : state;
}
