using UnityEngine;

/// <summary>
/// Points the camera at the avatar on start, so the scene works without
/// knowing the model's scale or facing direction in advance.
/// V switches between a face close-up and the whole body, and F swaps to the
/// other side if the camera ends up behind the head. By default the camera
/// then stays put (HeadLookAtCamera turns her head toward it instead); tick
/// Follow Avatar to have it track her smoothly.
/// </summary>
[RequireComponent(typeof(Camera))]
public class AvatarCameraFramer : MonoBehaviour
{
    [Tooltip("Renderer to frame in face view. If empty, uses the face found by the BlendShapeKeyController in the scene.")]
    public Renderer focus;

    [Tooltip("How much of the view height the framed bounds should fill.")]
    [Range(0.1f, 1f)] public float fill = 0.6f;

    [Tooltip("Look at the avatar from its +Z side (Unity's usual character front).")]
    public bool viewFromPositiveZ = true;

    public bool fullBody;

    [Tooltip("Keep re-framing every frame. Off: frame once, then only when V or F is pressed.")]
    public bool followAvatar;

    [Tooltip("How quickly the camera catches up with the avatar. 0 snaps.")]
    public float followSmoothing = 4f;

    public KeyCode flipKey = KeyCode.F;
    public KeyCode bodyToggleKey = KeyCode.V;

    Camera cam;
    Renderer[] bodyRenderers;
    Vector3 lookCenter;
    float distance;
    bool snap = true;

    void Start()
    {
        cam = GetComponent<Camera>();
        if (focus == null)
        {
            var controller = FindObjectOfType<BlendShapeKeyController>();
            if (controller != null)
                focus = controller.target;
        }
        if (focus != null)
            bodyRenderers = focus.transform.root.GetComponentsInChildren<Renderer>();
    }

    void Update()
    {
        if (BlendShapeKeyController.TypingInUI)
            return;

        if (Input.GetKeyDown(flipKey))
        {
            viewFromPositiveZ = !viewFromPositiveZ;
            snap = true;
        }
        if (Input.GetKeyDown(bodyToggleKey))
        {
            fullBody = !fullBody;
            snap = true;
        }
    }

    // After animation, so the bounds match this frame's pose.
    void LateUpdate()
    {
        if (focus == null || (!followAvatar && !snap))
            return;

        Bounds bounds = TargetBounds();
        float radius = Mathf.Max(bounds.extents.y, bounds.extents.x);
        float halfFov = cam.fieldOfView * 0.5f * Mathf.Deg2Rad;
        float targetDistance = radius / (Mathf.Tan(halfFov) * fill);

        float t = snap || followSmoothing <= 0f ? 1f : 1f - Mathf.Exp(-followSmoothing * Time.deltaTime);
        lookCenter = Vector3.Lerp(lookCenter, bounds.center, t);
        distance = Mathf.Lerp(distance, targetDistance, t);
        snap = false;

        Vector3 dir = viewFromPositiveZ ? Vector3.forward : Vector3.back;
        transform.position = lookCenter + dir * distance;
        transform.rotation = Quaternion.LookRotation(-dir, Vector3.up);
        cam.nearClipPlane = Mathf.Max(0.01f, distance * 0.05f);
    }

    Bounds TargetBounds()
    {
        if (!fullBody || bodyRenderers == null || bodyRenderers.Length == 0)
            return focus.bounds;

        Bounds bounds = focus.bounds;
        foreach (Renderer r in bodyRenderers)
            bounds.Encapsulate(r.bounds);
        return bounds;
    }
}
