# Unity avatar (ChatUI 3D front end)

Unity 2021.3.45f1 project for the 3D companion front end. Right now it loads
the avatar and lets you trigger face blend shapes and body animations from the keyboard; later the
same controller will be driven by the ChatUI server (expressions, lip sync).

## Setup

1. The avatar model and its animations are third-party, so they aren't committed.
   Copy them in **before** opening the project:
   - `miomio10261500.fbx` (renamed from `mio mio 10261500.fbx`, no spaces) into `ChatUIAvatar/Assets/Models/` (its committed `.meta` keeps the scene reference working).
   - `miorine tx.png` into `Assets/Models/` as well. Newer exports embed it, but the importer falls back to this copy.
   - Mixamo animation FBXs (`Happy Idle.fbx`, `Angry.fbx`, `Excited.fbx`, `Joyful Jump.fbx`,
     `Female Sitting Pose.fbx`, `Female Laying Pose.fbx`) into `Assets/Resources/Animations/`.
2. Open `ChatUIAvatar` in Unity Hub with 2021.3.45f1, open `Assets/Scenes/Main.unity`, press Play.

If the model shows as "Missing Prefab" (the FBX was added after first open),
delete that object and use the menu **ChatUI > Add Avatar To Scene**.

The model and every FBX in `Assets/Resources/Animations/` are imported with the
Humanoid rig (an editor postprocessor sets this up), so Mixamo clips on the
standard `mixamorig` skeleton retarget onto her. Root motion is baked into the
pose, so she stays in place, and every clip's start is centred on her and faces the same way. If she doesn't animate, check the model's
**Rig** tab > **Configure** for the bone mapping (use **Enforce T-Pose** if the
arms look wrong).

## Controls

In Play mode the left panel lists every shape key on the face mesh, read from
the model itself, so a re-exported FBX with more shape keys shows them all
automatically. Click a row to toggle it, use the filter box to search, the
header to collapse the panel, and "One at a time" to allow mixing. The right
panel does the same for animations.

Keyboard shortcuts for the face, on `AvatarController` > Blend Shape Key Controller:

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
- The camera frames her once and stays put; her neck and head turn to look at it on top of any animation. `L` toggles the head tracking. Tune it in **Head Look At Camera** (weight, neck share, max angle, smoothing); tick **Character Faces Negative Z** if she looks away instead. **Body Turn** (0–1) on the same component also turns her whole body toward the camera, including after you move her.
- `V` reframes between face close-up and full body, `F` swaps the camera to the other side. Tick **Follow Avatar** on the camera to have it track her instead.
- Under **Keep In Place** on Avatar Animation Player, **Align Start** shifts each clip so it starts where she stands (she can still walk around during it). **Lock Forward Back** (on), **Lock Sideways** and **Lock Up Down** pin her hips on that axis for the whole clip.
- Clicking an animation in the panel plays it at normal speed. Speeds are set per motion set (below), so the same clip can be fast in one set and slow in another.
- Rebind keys, shape names or animations in the lists on `AvatarController`. New
  Mixamo clips just need to go in `Assets/Resources/Animations/`: with **Auto Add
  Clips** on they show up in the panel by file name (looping if the name has
  idle, pose or loop). Add an entry only to give one a key, speed or loop setting.

Face mesh shape keys: `miomio10261500` has 88, including the tsundere (`tsun ...`),
kuudere (`kuu ...`) and happy (`laugh`, `giggle`, `beaming`, `playful`, `adoring` ...) sets. The in-game panel always shows the current list.

## Motion sets

A motion set fuses a body animation, its speed and a group of shape keys under
one name (e.g. `happy` = Excited animation + `happy` shape key). Edit them in
**Motion Set Library** on `AvatarController`:

- **Name**: what the panel and the API call it. **Description**: when to use it; this is what the LLM will see.
- **Animation**: dropdown of the animations inside the avatar's own FBX (Blender actions, by name) and the clips in `Assets/Resources/Animations` (empty keeps the current animation).
- **Then Loop**: after a play-once animation, keep looping this one (e.g. `sit down on floor` then `sit on floor loop`).
- **Speed**: 1 = normal, 0.5 = half, 2 = double, for this set only.
- **Loop**: the clip's own setting, or force loop / play once.
- **Duration**: seconds before going back to the default set. 0 stays until another set plays, or until a play-once animation finishes.
- **Shape Keys**: dropdown of the face mesh's shape keys, each with a weight.

**Default Motion Set** plays at start and whenever a set ends. Sets are listed
in the top-right panel; click one to play it. Inspector edits made during Play
are lost when you stop, so edit outside Play.

## Lip sync

**Lip Sync Controller** on `AvatarController` lists the shape keys used for
each mouth shape while talking (A, I, U, E, O by default, mapped to aa, ih,
ou, ee, oh). Nothing drives it yet; once text to speech is linked, ChatUI will
send mouth shapes through the API below. Lip sync is added on top of the
motion set's shape keys, so she can talk while smiling.

## API (for ChatUI later)

**Avatar Api Server** on `AvatarController` listens on
`http://localhost:7870` (change **Port**; tick **Allow Remote** if ChatUI
runs on another PC). It works in Play mode and in the desktop companion.

| Request | Does |
|---|---|
| `GET /api/motionsets` | Lists set names and descriptions (to put in the LLM prompt) |
| `POST /api/motionset` `{"name":"happy"}` | Plays a set (also `GET /api/motionset?name=happy`) |
| `GET /api/status` | Current set, animation, speed, whether she's speaking |
| `GET /api/visemes` | Lip sync mouth shape names |
| `POST /api/lipsync` `{"viseme":"A","amount":0.8}` | Opens one mouth shape (0-1); `{"visemes":[...]}` for several |
| `POST /api/lipsync/clear` | Closes the mouth |
| `GET /api/animations`, `GET /api/shapekeys` | What's available |

Quick test from PowerShell while she's running:

```powershell
Invoke-RestMethod http://localhost:7870/api/motionsets
Invoke-RestMethod http://localhost:7870/api/motionset -Method Post -Body '{"name":"happy"}' -ContentType application/json
```

The mouth closes on its own if no lip sync update arrives for half a second.

## Desktop companion (Windows)

`ChatUI > Build Desktop Companion` sets the player settings transparency needs
(DirectX 11, DXGI flip model off, windowed, run in background) and builds the
scene that's open in the editor to
`ChatUIAvatar/Builds/DesktopCompanion/`. Run the .exe there: she appears on
your desktop with no window around her, always on top, and clicks go through
to whatever is behind except on her and the panels.

- Right-drag her to move her, Ctrl + right-drag to rotate her (Ctrl+R or the **Reset rotation** button in the Animations panel resets it), scroll over her to resize.
- `Tab` hides or shows the panels, `Ctrl+Q` (or Alt+F4) quits.
- Everything else (shape keys, animations, head tracking) works as in the editor.

Transparency defaults to **ColorKey** (the background is magenta and Windows
hides that color), which works on any GPU. If you want smoother edges, set
**Transparency** to **Alpha** on the camera's Desktop Companion Window
component and rebuild; some PCs show a black or white background with it.

This only happens in a Windows build. In the editor the scene keeps its normal
background so you can keep working on it.

## Look

Her materials use `ChatUI/Toon Double Sided`: the flat palette colors plus a
saturation/brightness/contrast boost, a soft tinted cel shadow from the
Directional Light, and a rim light. Tune it live in Play mode under **Look**
on `AvatarController` > Avatar Double Sided (Play-mode changes reset on stop,
so copy the values you like afterwards). Rotate the Directional Light to move
the shadow side.
