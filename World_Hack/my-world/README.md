# X2 Web Client

A Next.js and TypeScript front end for streaming video editing with Reactor X2. It sends a live video source (webcam, clip or still image) to the model, applies text prompts as edits, and displays the edited stream. It is also used here as the browser-side path for the Sim Genie rig (see `ROS_BRIDGE.md`).

## Setup

```bash
cp .env.example .env.local   # set REACTOR_API_KEY
pnpm install
pnpm dev                     # http://localhost:3000
```

`REACTOR_API_KEY` is the only required variable. It stays on the server; the browser receives a short-lived token minted by `app/api/reactor/token/route.ts`.

## Features

- **Sources:** webcam, video clip (preset or local file) or still image. All are published to the model's `source` track, so the source and edited panes share one feed.
- **Prompting:** a prompt arms generation, and re-prompting mid-stream applies the new edit at the next chunk boundary.
- **Reference image:** an uploaded image conditions the output. Uploads are cropped down to multiples of 4 pixels in each dimension, which the runtime requires.
- **Pointer steering:** dragging on the output sends normalised coordinates via `set_pointer`.
- **Keep backlog:** toggles between editing every queued frame and dropping stale frames for lower latency.
- **Clip capture:** records the last N seconds of the stream.

Keep the source and reference image in the same orientation. The output resolution follows the source, and a mismatched reference distorts the result. All bundled assets are landscape 16:9.

## Architecture

The model is the source of truth. It broadcasts a `state_update` snapshot on connect and after each change, and the UI renders from the reduced `X2UiState` (`app/lib/state.ts`, `app/lib/types.ts`).

Commands sent: `set_prompt`, `set_reference_image`, `set_pointer`, `set_keep_backlog`, `reset`. Tracks: input `source`, output `main_video`. The typed client comes from [`@reactor-models/x2`](https://www.npmjs.com/package/@reactor-models/x2).

| Path | Purpose |
|---|---|
| `app/X2App.tsx` | Provider, layout and handlers for discrete model events |
| `app/lib/state.ts`, `types.ts` | UI state and the reducer for `state_update` |
| `app/components/SourcePanel.tsx` | Source selection, keep-backlog and reset |
| `app/components/*Source.tsx` | Webcam, video and image capture into `source` |
| `app/components/useSourcePublisher.ts` | Single owner of the `source` track |
| `app/components/Prompt.tsx` | Prompt input and presets |
| `app/components/ReferenceImage.tsx` | Reference upload and preview |
| `app/components/PointerOverlay.tsx`, `PointerPanel.tsx` | Pointer steering and readout |
| `app/components/Stage.tsx` | Edited output, split view for video and image modes |
| `app/components/SnapClip.tsx` | Clip recording |
| `app/api/reactor/token/route.ts` | Server-side token minting |
| `app/lib/ros/` | ROS bridge, rosbridge client and MJPEG reader |

## Stack

Next.js 15, React 19, TypeScript, Tailwind v4, `@reactor-team/js-sdk`, `@reactor-team/ui`.

Model documentation: <https://docs.reactor.inc/model-api-reference/xmax/overview>
