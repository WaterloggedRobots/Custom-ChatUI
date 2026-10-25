# Unity avatar (ChatUI 3D front end)

Unity 2021.3.45f1 project for the 3D companion front end. Right now it loads
the avatar and lets you trigger face blend shapes and body animations from the keyboard; later the
same controller will be driven by the ChatUI server (expressions, lip sync).

## Setup

1. The avatar model and its animations are third-party, so they aren't committed.
   Copy them in **before** opening the project:
   - `miomio10251800.fbx` into `ChatUIAvatar/Assets/Models/` (its committed `.meta` keeps the scene reference working).
   - `miorine tx.png` into `Assets/Models/` as well. Newer exports embed it, but the importer falls back to this copy.
   - Mixamo animation FBXs (`Happy Idle.fbx`, `Angry.fbx`, `Excited.fbx`, `Joyful Jump.fbx`,
     `Female Sitting Pose.fbx`, `Female Laying Pose.fbx`) into `Assets/Resources/Animations/`.
2. Open `ChatUIAvatar` in Unity Hub with 2021.3.45f1, open `Assets/Scenes/Main.unity`, press Play.

If the model shows as "Missing Prefab" (the FBX was added after first open),
delete that object and use the menu **ChatUI > Add Avatar To Scene**.

The animation FBXs must use the same skeleton as the model (Mixamo files made
by uploading this model do). The rig is Generic, so bones are matched by name.

## Controls

Face (shape keys), on `AvatarController` > Blend Shape Key Controller:

| Key | Shape key | Shift + key | Shape key |
| --- | --- | --- | --- |
| 1 | blink | Shift+1 | joy |
| 2 | happy | Shift+2 | smug |
| 3 | angry | Shift+3 | pout |
| 4 | sad | Shift+4 | cry |
| 5 | surprised | Shift+5 | sleepy |
| 6 | aa | Shift+6 | embarrassed |
| 7 | ih | Shift+7 | shocked |
| 8 | ou | Shift+8 | annoyed |
| 9 | ee | Shift+9 | grin |
| 0 | oh | Shift+0 | furious |

Body (animations), on `AvatarController` > Avatar Animation Player:

| Key | Animation |
| --- | --- |
| F1 | Happy Idle (default, loops) |
| F2 | Angry (once, then back to idle) |
| F3 | Excited (once) |
| F4 | Joyful Jump (once) |
| F5 | Female Sitting Pose (holds) |
| F6 | Female Laying Pose (holds) |

- Number keys toggle (numpad works too). Untick **Toggle Mode** to make them hold-to-show instead.
- `-` / `=` step through every shape key on the face mesh one by one.
- `Backspace` clears all shapes.
- `V` switches the camera between face close-up and full body, `F` swaps it to the other side.
- Each animation entry has a **Speed** (1 = normal, 0.5 = half, 2 = double). You can change it during Play and see it immediately, but Play-mode edits are reset when you stop, so set the final values outside Play.
- Rebind keys, shape names or animations in the lists on `AvatarController`. New
  Mixamo clips just need to go in `Assets/Resources/Animations/` and get an entry.

Face mesh shape keys in `miomio10251800` (63): blink L, blink R, blink 2 L, blink 2 R,
jaw open, jaw open A, mouth wide.001, mouth narrow, eye size, eye wide, brows
angry, brows sad, brows surprised, あ, い, う, え, お, まばたき, 笑い, ウィンク,
ウィンク右, ウィンク２, ｳｨﾝｸ２右, びっくり, 怒り, 困る, 上, aa, ih, ou, ee, oh,
blink, blinkLeft, blinkRight, happy, angry, sad, surprised, mouth smile, mouth
frown, 口角上げ, 口角下げ, にやり, じと目, なごみ, joy, smile soft, smiling, grin,
smug, sad face, cry, angry face, mad, furious, annoyed, shocked, scared, sleepy,
embarrassed, pout.
