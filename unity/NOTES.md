# Unity side: status

The Unity project lives outside this repo. `unity/Assets/MarsRig/` holds the
scripts that go into it; `unity/README.md` is the setup guide.

## Status

The scripts have not yet been opened in Unity. The C# compiles against stub
types whose signatures were checked against the ROS-TCP-Connector source, which
is not the same as building in the editor. To validate:

1. Add the ROS-TCP-Connector package and set the protocol to ROS 2.
2. Copy in `Assets/MarsRig/`.
3. Run `Mars Rig > Build Scored Scene`.
4. Press Play, run `Mars Rig > Check Camera Alignment`, and record the result.

The Unity project inspected so far had no `Assets/` and no
`com.unity.robotics.ros-tcp-connector` entry in `Packages/manifest.json`, so
nothing was publishing to ROS yet.

## Render pipeline

The project uses the Built-in Render Pipeline (no `com.unity.render-pipelines.*`
package in the manifest).

`DualCapture` swaps each renderer's material to a flat `Unlit/Color` for the
label pass. This works in any pipeline and avoids a replacement shader with
`RenderType` tags, which are Built-in-only. If the project moves to URP,
`Camera.Render()` into a RenderTexture still works, but confirm that
`Unlit/Color` still resolves via `Shader.Find`; URP uses
`Universal Render Pipeline/Unlit`.

## Remaining work

- Terrain material: currently a flat `Standard` colour. A regolith texture
  would give the classical segmenter more to key off and raise the ceiling.
- Rover body: currently a cube. It rarely appears in frame, but it is visible
  in the demo video.
- Depth camera (Camera 3) is not built; the evaluation uses IoU only.
