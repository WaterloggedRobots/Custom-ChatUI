using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Playables;

/// <summary>
/// Plays body animations on the avatar from a clickable on-screen list, or
/// from the keyboard (F1-F6 by default).
///
/// Clips are loaded from FBX files under Assets/Resources/Animations, so new
/// Mixamo downloads only need to be dropped in that folder and bound here;
/// no Animator Controller is needed. The animation FBXs must share the
/// avatar's skeleton (same bone names), since the rig is Generic.
///
/// Looping entries play until another one is chosen. One-shot entries play
/// once, then fade back to the default (first looping) entry. Each entry has
/// its own playback speed.
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
        [Tooltip("Playback speed: 1 is normal, 0.5 half speed, 2 double. Can be changed during Play.")]
        [Min(0.01f)] public float speed = 1f;

        [NonSerialized] public AnimationClip clip;

        // Unity uses this when deserializing, so new fields get their defaults.
        public Entry() { }

        public Entry(KeyCode key, string resourcePath, bool loop, float speed = 1f)
        {
            this.key = key;
            this.resourcePath = resourcePath;
            this.loop = loop;
            this.speed = speed;
        }

        public string Label => resourcePath.Substring(resourcePath.LastIndexOf('/') + 1);
    }

    [Tooltip("Avatar Animator. If empty, the Animator on the face mesh's root is used (added if missing).")]
    public Animator animator;

    [Tooltip("Seconds to crossfade between animations.")]
    public float fadeTime = 0.3f;

    public bool showOverlay = true;

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
    int activeSlot;
    float fade = 1f;
    Entry current;
    Entry defaultEntry;
    bool panelExpanded = true;
    Vector2 scroll;
    GUIStyle rowStyle;
    GUIStyle activeRowStyle;

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

        foreach (Entry e in entries)
        {
            e.clip = LoadClip(e);
            if (e.clip == null)
                Debug.LogWarning($"AvatarAnimationPlayer: no clip found at Resources/{e.resourcePath}.");
            else if (defaultEntry == null && e.loop)
                defaultEntry = e;
        }

        graph = PlayableGraph.Create("AvatarAnimation");
        graph.SetTimeUpdateMode(DirectorUpdateMode.GameTime);
        mixer = AnimationMixerPlayable.Create(graph, 2);
        AnimationPlayableOutput.Create(graph, "Avatar", animator).SetSourcePlayable(mixer);
        graph.Play();

        if (defaultEntry != null)
            Play(defaultEntry);
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
        active.SetSpeed(current.speed);
        double time = active.GetTime();
        double length = current.clip.length;
        if (length > 0 && time >= length)
        {
            if (current.loop)
                active.SetTime(time % length);
            else if (defaultEntry != null && current != defaultEntry)
                Play(defaultEntry);
        }

        if (fade < 1f)
        {
            fade = fadeTime > 0f ? Mathf.Min(1f, fade + Time.deltaTime / fadeTime) : 1f;
            mixer.SetInputWeight(activeSlot, fade);
            mixer.SetInputWeight(1 - activeSlot, 1f - fade);
        }
    }

    /// <summary>Crossfades to an entry. Also usable from other scripts.</summary>
    public void Play(Entry entry)
    {
        if (entry?.clip == null)
            return;

        int next = 1 - activeSlot;
        if (slots[next].IsValid())
        {
            graph.Disconnect(mixer, next);
            slots[next].Destroy();
        }

        slots[next] = AnimationClipPlayable.Create(graph, entry.clip);
        slots[next].SetApplyFootIK(false);
        slots[next].SetSpeed(entry.speed);
        graph.Connect(slots[next], 0, mixer, next);

        // With nothing playing yet there is nothing to fade from.
        bool first = current == null;
        activeSlot = next;
        current = entry;
        fade = first ? 1f : 0f;
        mixer.SetInputWeight(activeSlot, fade);
        mixer.SetInputWeight(1 - activeSlot, 1f - fade);
    }

    /// <summary>Plays an entry by its label (the FBX file name). Returns false if not found.</summary>
    public bool Play(string label)
    {
        Entry entry = entries.Find(e => e.Label == label);
        if (entry == null)
            return false;
        Play(entry);
        return true;
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
        float height = panelExpanded ? Mathf.Min(Screen.height - 20f, 50f + entries.Count * 24f) : 34f;
        var panel = new Rect(Screen.width - width - 10, 10, width, height);
        BlendShapeKeyController.DrawPanelBackground(panel);
        GUILayout.BeginArea(panel, GUI.skin.box);

        if (GUILayout.Button($"{(panelExpanded ? "\u25BC" : "\u25BA")} Animations ({entries.Count})"))
            panelExpanded = !panelExpanded;

        if (panelExpanded)
        {
            scroll = GUILayout.BeginScrollView(scroll);
            foreach (Entry e in entries)
            {
                string label = e.clip == null ? $"{e.Label} (missing)" : e.Label;
                if (!e.loop)
                    label += " (once)";
                if (!Mathf.Approximately(e.speed, 1f))
                    label += $" x{e.speed:0.##}";
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
