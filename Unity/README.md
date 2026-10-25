# Unity avatar (ChatUI 3D front end)

Unity 2021.3.45f1 project for the 3D companion front end. Right now it loads
the avatar and lets you trigger face blend shapes from the keyboard; later the
same controller will be driven by the ChatUI server (expressions, lip sync).

## Setup

1. The avatar model is third-party, so it isn't committed. Copy your
   `mio_face_demo.fbx` into `ChatUIAvatar/Assets/Models/` **before** opening the
   project (the committed `.meta` next to it keeps the scene reference working).
2. Optional: copy the texture `miorine tx.png` into `Assets/Models/` too. The
   FBX references it but doesn't embed it, so without it the model is untextured.
3. Open `ChatUIAvatar` in Unity Hub with 2021.3.45f1, open `Assets/Scenes/Main.unity`, press Play.

If the model shows as "Missing Prefab" (the FBX was added after first open),
delete that object and use the menu **ChatUI > Add Avatar To Scene**.

## Controls

| Key | Blend shape |
| --- | --- |
| 1 | blink |
| 2 | happy |
| 3 | angry |
| 4 | sad |
| 5 | surprised |
| 6 | aa |
| 7 | ih |
| 8 | ou |
| 9 | ee |
| 0 | oh |

- Number keys toggle (numpad works too). Untick **Toggle Mode** on
  `AvatarController` to make them hold-to-show instead.
- `-` / `=` step through all 40 shapes on the face mesh one by one.
- `Backspace` clears all shapes. `F` swaps the camera to the other side of the head.
- Rebind keys or shape names in the `Bindings` list on `AvatarController`.

Face mesh blend shapes in this model: blink L, blink R, blink 2 L, blink 2 R,
jaw open, jaw open A, mouth wide.001, mouth narrow, eye size, eye wide, brows
angry, brows sad, brows surprised, あ, い, う, え, お, まばたき, 笑い, ウィンク,
ウィンク右, ウィンク２, ｳｨﾝｸ２右, びっくり, 怒り, 困る, 上, aa, ih, ou, ee, oh,
blink, blinkLeft, blinkRight, happy, angry, sad, surprised.
