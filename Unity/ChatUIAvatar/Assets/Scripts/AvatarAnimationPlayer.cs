using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Playables;
using UnityEngine.Serialization;

/// <summary>
/// Plays body animations on the avatar from a clickable on-screen list, or
/// from the keyboard (F1-F6 by default).
///
/// Clips are loaded from FBX files under Assets/Resources/Animations, so new
/// Mixamo downloads only need to be dropped in that folder; with Auto Add
/// Clips on they are listed by file name without any setup, and no Animator
/// Controller is needed. The avatar and the animation FBXs use the Humanoid
/// rig, so clips made for other skeletons (e.g. mixamorig) retarget onto her.
///
/// Looping entries play until another one is chosen. One-shot entries play
/// once, then fade back to the default (first looping) entry. Playback speed
/// is set per motion set (MotionSetLibrary), so the same clip can play at
/// different speeds in different sets; clicking a clip here plays it at 1x.
/// </summary>
public class AvatarAnimationPlayer : MonoBehaviour
{
    [Serializable]
    public class Entry
    {
        public KeyCode key;
        [Tooltip("Path under a Resources folder, without extension, e.g. Animations/Happy Idle.")]
        public string resourcePath;
        [Tooltip("Take to use from the FBX. Empty picks 'mixamo.com', else the first clip.")]
        public string clipName;
        public bool loop;

        [NonSerialized] public AnimationClip clip;
        // How far the clip's first frame puts her hips from where she stands.
        [NonSerialized] public Vector3 startOffset;

        // Unity uses this when deserializing, so new fields get their defaults.
        public Entry() { }

        public Entry(KeyCode key, string resourcePath, bool loop)
        {
            this.key = key;
            this.resourcePath = resourcePath;
            this.loop = loop;
        }

        public string Label => resourcePath.Substring(resourcePath.LastIndexOf('/') + 1);
    }

    [Tooltip("Avatar Animator. If empty, the Animator on the face mesh's root is used (added if missing).")]
    public Animator animator;

    [Tooltip("Also list every clip under Resources/<Auto Add Folder> that has no entry below (looping if its name has idle, pose or loop).")]
    public bool autoAddClips = true;
    public string autoAddFolder = "Animations";

    [Tooltip("Seconds to crossfade between animations.")]
    public float fadeTime = 0.3f;

    public bool showOverlay = true;

    [Header("Keep In Place")]
    [Tooltip("Shift each clip so its first frame lines up with where she stands. She can still move around during the clip; it just doesn't start off to one side.")]
    public bool alignStart = true;
    [Tooltip("Stop animations moving her forward and back (along her own Z axis).")]
    [FormerlySerializedAs("lockZ")] public bool lockForwardBack = true;
    [Tooltip("Stop animations moving her sideways (along her own X axis), e.g. walking to the side.")]
    public bool lockSideways;
    [Tooltip("Stop animations moving her up and down. Off by default, since sitting or getting up needs it.")]
    [FormerlySerializedAs("lockY")] public bool lockUpDown;

    public List<Entry> entries = new List<Entry>
    {
        new Entry(KeyCode.F1, "Animations/Happy Idle", true),
        new Entry(KeyCode.F2, "Animations/Angry", false),
        new Entry(KeyCode.F3, "Animations/Excited", false),
        new Entry(KeyCode.F4, "Animations/Joyful Jump", false),
        new Entry(KeyCode.F5, "Animations/Female Sitting Pose", true),
        new Entry(KeyCode.F6, "Animations/Female Laying Pose", true),
    };

    PlayableGraph graph;
    AnimationMixerPlayable mixer;
    readonly AnimationClipPlayable[] slots = new AnimationClipPlayable[2];
    readonly Entry[] slotEntries = new Entry[2];
    int activeSlot;
    float fade = 1f;
    Entry current;
    float currentSpeed = 1f;
    bool currentLoop;
    bool finishedRaised;
    Entry defaultEntry;
    bool panelExpanded = true;
    Vector2 scroll;
    GUIStyle rowStyle;
    GUIStyle activeRowStyle;
    Transform hips;
    HeadLookAtCamera look;
    Vector3 restHipsLocal;

    void Start()
    {
        if (animator == null)
            animator = FindAvatarAnimator();
        if (animator == null)
        {
            Debug.LogWarning("AvatarAnimationPlayer: no avatar found to animate.");
            enabled = false;
            return;
        }
        animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;
        animator.applyRootMotion = false;
        look = FindObjectOfType<HeadLookAtCamera>();

        // Start runs before the first animation frame, so this is the rest pose.
        hips = animator.isHuman ? animator.GetBoneTransform(HumanBodyBones.Hips) : null;
        if (hips != null)
            restHipsLocal = animator.transform.InverseTransformPoint(hips.position);

        foreach (Entry e in entries)
        {
            e.clip = LoadClip(e);
            if (e.clip == null)
                Debug.LogWarning($"AvatarAnimationPlayer: no clip found at Resources/{e.resourcePath}.");
            else if (defaultEntry == null && e.loop)
                defaultEntry = e;
        }
        if (autoAddClips)
            AddUnlistedClips();

        graph = PlayableGraph.Create("AvatarAnimation");
        graph.SetTimeUpdateMode(DirectorUpdateMode.GameTime);
        mixer = AnimationMixerPlayable.Create(graph, 2);
        AnimationPlayableOutput.Create(graph, "Avatar", animator).SetSourcePlayable(mixer);
        graph.Play();

        if (defaultEntry != null)
            Play(defaultEntry);

        if (hips != null)
            StartCoroutine(MeasureStartOffsetsWhenReady());
    }

    // In a build the Animator isn't ready to be posed yet during Start, so
    // measuring there reads the rest pose for every clip. Wait one frame.
    IEnumerator MeasureStartOffsetsWhenReady()
    {
        yield return null;
        MeasureStartOffsets();
    }

    void OnDestroy()
    {
        if (graph.IsValid())
            graph.Destroy();
    }

    void Update()
    {
        if (!BlendShapeKeyController.TypingInUI)
        {
            foreach (Entry e in entries)
            {
                if (e.clip != null && Input.GetKeyDown(e.key))
                    Play(e);
            }
        }

        if (current == null)
            return;

        AnimationClipPlayable active = slots[activeSlot];
        double time = active.GetTime();
        double length = current.clip.length;
        if (length > 0 && time >= length)
        {
            if (currentLoop)
                active.SetTime(time % length);
            else if (!finishedRaised)
            {
                finishedRaised = true;
                Entry finished = current;
                Finished?.Invoke(finished);
                // Whoever handled Finished may already have started something else.
                if (current == finished && defaultEntry != null && current != defaultEntry)
                    Play(defaultEntry);
            }
        }

        if (fade < 1f)
        {
            fade = fadeTime > 0f ? Mathf.Min(1f, fade + Time.deltaTime / fadeTime) : 1f;
            mixer.SetInputWeight(activeSlot, fade);
            mixer.SetInputWeight(1 - activeSlot, 1f - fade);
        }
    }

    // After the Animator has posed her, shift the hips so each clip starts
    // where she stands, and pin them on the locked axes.
    void LateUpdate()
    {
        if (hips == null)
            return;

        Transform root = animator.transform;
        Vector3 local = root.InverseTransformPoint(hips.position);
        if (alignStart)
        {
            // Blend the two clips' offsets during a crossfade, so the
            // correction fades along with the clips.
            for (int i = 0; i < 2; i++)
            {
                if (slotEntries[i] != null && slots[i].IsValid())
                    local -= slotEntries[i].startOffset * mixer.GetInputWeight(i);
            }
        }
        if (lockSideways) local.x = restHipsLocal.x;
        if (lockUpDown) local.y = restHipsLocal.y;
        if (lockForwardBack) local.z = restHipsLocal.z;
        hips.position = root.TransformPoint(local);
    }

    // Poses her on each clip's first frame to see where it puts her hips,
    // then puts the skeleton back as it was. Only the ground plane offset is
    // kept, so sitting and lying clips keep their height. Runs after Update,
    // so the regular graph poses her again before anything is drawn.
    void MeasureStartOffsets()
    {
        Transform root = animator.transform;
        Transform[] bones = root.GetComponentsInChildren<Transform>();
        var positions = new Vector3[bones.Length];
        var rotations = new Quaternion[bones.Length];
        for (int i = 0; i < bones.Length; i++)
        {
            positions[i] = bones[i].localPosition;
            rotations[i] = bones[i].localRotation;
        }

        int measured = 0, failed = 0;
        foreach (Entry e in entries)
        {
            if (e.clip == null)
                continue;

            PlayableGraph sampler = PlayableGraph.Create("AvatarAnimationSampler");
            var playable = AnimationClipPlayable.Create(sampler, e.clip);
            playable.SetApplyFootIK(false);
            AnimationPlayableOutput.Create(sampler, "Sample", animator).SetSourcePlayable(playable);
            hips.position = root.TransformPoint(restHipsLocal);
            sampler.Evaluate(0f);
            Vector3 sampled = root.InverseTransformPoint(hips.position);
            sampler.Destroy();

            // If the graph didn't pose her, try sampling the clip directly.
            if (sampled == restHipsLocal)
            {
                e.clip.SampleAnimation(animator.gameObject, 0f);
                sampled = root.InverseTransformPoint(hips.position);
            }
            if (sampled == restHipsLocal)
            {
                failed++;
                continue;
            }

            Vector3 offset = sampled - restHipsLocal;
            offset.y = 0f;
            e.startOffset = offset;
            measured++;
        }
        Debug.Log($"AvatarAnimationPlayer: aligned the start of {measured} clips" +
                  (failed > 0 ? $", could not measure {failed}." : "."));

        for (int i = 0; i < bones.Length; i++)
        {
            bones[i].localPosition = positions[i];
            bones[i].localRotation = rotations[i];
        }
    }

    /// <summary>Raised when a one-shot clip reaches its end, before falling back to the default clip.</summary>
    public event Action<Entry> Finished;

    /// <summary>Raised whenever a clip starts, from any source (panel, keys, motion sets).</summary>
    public event Action<Entry> Started;

    /// <summary>The clip playing now, or null.</summary>
    public Entry Current => current;

    public float CurrentSpeed => currentSpeed;

    /// <summary>Finds an entry by label (the FBX file name), ignoring case.</summary>
    public Entry Find(string label)
    {
        return entries.Find(e => string.Equals(e.Label, label, StringComparison.OrdinalIgnoreCase));
    }

    /// <summary>Crossfades to an entry at normal speed with its own loop setting.</summary>
    public void Play(Entry entry)
    {
        Play(entry, 1f, entry != null && entry.loop);
    }

    /// <summary>Crossfades to an entry at the given speed, looping or not.</summary>
    public void Play(Entry entry, float speed, bool loop)
    {
        if (entry?.clip == null || !graph.IsValid())
            return;

        int next = 1 - activeSlot;
        if (slots[next].IsValid())
        {
            graph.Disconnect(mixer, next);
            slots[next].Destroy();
        }

        slots[next] = AnimationClipPlayable.Create(graph, entry.clip);
        slots[next].SetApplyFootIK(false);
        slots[next].SetSpeed(Mathf.Max(0.01f, speed));
        graph.Connect(slots[next], 0, mixer, next);
        slotEntries[next] = entry;

        // With nothing playing yet there is nothing to fade from.
        bool first = current == null;
        activeSlot = next;
        current = entry;
        currentSpeed = Mathf.Max(0.01f, speed);
        currentLoop = loop;
        finishedRaised = false;
        fade = first ? 1f : 0f;
        mixer.SetInputWeight(activeSlot, fade);
        mixer.SetInputWeight(1 - activeSlot, 1f - fade);
        Started?.Invoke(entry);
    }

    /// <summary>Plays an entry by its label (the FBX file name). Returns false if not found.</summary>
    public bool Play(string label)
    {
        Entry entry = Find(label);
        if (entry == null)
            return false;
        Play(entry);
        return true;
    }

    // Adds every clip in Resources/Animations that has no entry yet, so new
    // FBXs dropped into that folder show up in the panel without setup. The
    // import postprocessor names each clip after its file.
    void AddUnlistedClips()
    {
        var added = new List<Entry>();
        foreach (AnimationClip clip in Resources.LoadAll<AnimationClip>(autoAddFolder))
        {
            if (clip.name.StartsWith("__preview__", StringComparison.Ordinal))
                continue;
            if (entries.Exists(e => e.clip == clip || e.Label == clip.name))
                continue;

            string lower = clip.name.ToLowerInvariant();
            bool loop = lower.Contains("idle") || lower.Contains("pose") || lower.Contains("loop");
            added.Add(new Entry(KeyCode.None, autoAddFolder + "/" + clip.name, loop) { clip = clip });
        }

        added.Sort((a, b) => string.Compare(a.Label, b.Label, StringComparison.OrdinalIgnoreCase));
        entries.AddRange(added);
        if (defaultEntry == null)
            defaultEntry = added.Find(e => e.loop);
    }

    static AnimationClip LoadClip(Entry e)
    {
        AnimationClip fallback = null;
        foreach (AnimationClip clip in Resources.LoadAll<AnimationClip>(e.resourcePath))
        {
            // The editor exposes extra preview clips on model assets.
            if (clip.name.StartsWith("__preview__", StringComparison.Ordinal))
                continue;

            string wanted = string.IsNullOrEmpty(e.clipName) ? "mixamo.com" : e.clipName;
            if (clip.name == wanted)
                return clip;
            if (fallback == null)
                fallback = clip;
        }
        return string.IsNullOrEmpty(e.clipName) ? fallback : null;
    }

    Animator FindAvatarAnimator()
    {
        var face = FindObjectOfType<BlendShapeKeyController>();
        Transform root = face != null && face.target != null ? face.target.transform.root : null;
        if (root == null)
            return FindObjectOfType<Animator>();

        Animator found = root.GetComponentInChildren<Animator>();
        return found != null ? found : root.gameObject.AddComponent<Animator>();
    }

    void OnGUI()
    {
        if (!showOverlay || !BlendShapeKeyController.PanelsVisible || animator == null)
            return;

        if (rowStyle == null)
        {
            rowStyle = new GUIStyle(GUI.skin.button) { alignment = TextAnchor.MiddleLeft, fontSize = 13 };
            activeRowStyle = new GUIStyle(rowStyle) { fontStyle = FontStyle.Bold };
            activeRowStyle.normal.textColor = activeRowStyle.hover.textColor = new Color(1f, 0.85f, 0.3f);
        }

        float width = 260f;
        float height = panelExpanded ? Mathf.Min(Screen.height - 20f, 76f + entries.Count * 24f) : 34f;
        // Sits under the motion set panel when there is one.
        float top = MotionSetLibrary.RightPanelBottom + 10f;
        height = Mathf.Min(height, Screen.height - top - 10f);
        var panel = new Rect(Screen.width - width - 10, top, width, height);
        BlendShapeKeyController.DrawPanelBackground(panel);
        GUILayout.BeginArea(panel, GUI.skin.box);

        if (GUILayout.Button($"{(panelExpanded ? "\u25BC" : "\u25BA")} Animations ({entries.Count})"))
            panelExpanded = !panelExpanded;

        if (panelExpanded)
        {
            if (look != null && GUILayout.Button("Reset rotation"))
                look.ResetRotation();

            scroll = GUILayout.BeginScrollView(scroll);
            foreach (Entry e in entries)
            {
                string label = e.clip == null ? $"{e.Label} (missing)" : e.Label;
                if (!e.loop)
                    label += " (once)";
                if (e.key != KeyCode.None)
                    label += $"   [{e.key}]";

                GUI.enabled = e.clip != null;
                if (GUILayout.Button(label, e == current ? activeRowStyle : rowStyle))
                    Play(e);
                GUI.enabled = true;
            }
            GUILayout.EndScrollView();
        }

        GUILayout.EndArea();
    }
}
