# Unity side - status

The Unity project lives outside this repo. `unity/Assets/MarsRig/` holds the
scripts that go into it; `unity/README.md` is the setup guide.

## Status

Written, **never opened in Unity**. The C# compiles clean against stub types
whose signatures were verified against the real ROS-TCP-Connector source, which
is not the same as building in the editor. Someone with the editor needs to:

1. Add the ROS-TCP-Connector package and set the protocol to ROS 2.
2. Copy in `Assets/MarsRig/`.
3. `Mars Rig > Build Scored Scene`.
4. Press Play, run `Mars Rig > Check Camera Alignment`, and report the number.

The Unity project seen so far was an empty shell - no `Assets/`, and
`Packages/manifest.json` with no `com.unity.robotics.ros-tcp-connector` - so
nothing had reached ROS yet.

## Built-in Render Pipeline

`DualCapture` swaps each renderer's material to a flat `Unlit/Color` for the
label pass. That works in any pipeline, which is why it was chosen over a
replacement shader with `RenderType` tags - those are Built-in-only and their
tag semantics are easy to get subtly wrong.

The manifest showed no `com.unity.render-pipelines.*` package, so the project is
Built-in RP. If someone moves it to URP, `Camera.Render()` into a RenderTexture
still works, but check that `Unlit/Color` still resolves via `Shader.Find` -
URP renames it to `Universal Render Pipeline/Unlit`.

## Still to do

- Terrain material: currently a flat `Standard` colour. A real regolith texture
  would give the classical segmenter something to key off and raise the ceiling.
- The rover body is a cube. It barely appears in frame, so this is cosmetic -
  but it is the thing on camera in the demo video.
- Depth camera (Camera 3) is **not** built. 9.4 decided IoU only.
