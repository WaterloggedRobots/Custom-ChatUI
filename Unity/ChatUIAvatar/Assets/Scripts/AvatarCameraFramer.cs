using UnityEngine;

/// <summary>
/// Points the camera at the avatar's face on start, so the scene works
/// without knowing the model's scale or facing direction in advance.
/// Press F to swap to the other side if the camera ends up behind the head.
/// </summary>
[RequireComponent(typeof(Camera))]
public class AvatarCameraFramer : MonoBehaviour
{
    [Tooltip("Renderer to frame. If empty, uses the face found by the BlendShapeKeyController in the scene.")]
    public Renderer focus;

    [Tooltip("How much of the view height the face bounds should fill.")]
    [Range(0.1f, 1f)] public float fill = 0.6f;

    [Tooltip("Look at the avatar from its +Z side (Unity's usual character front).")]
    public bool viewFromPositiveZ = true;

    public KeyCode flipKey = KeyCode.F;

    Camera cam;

    void Start()
    {
        cam = GetComponent<Camera>();
        if (focus == null)
        {
            var controller = FindObjectOfType<BlendShapeKeyController>();
            if (controller != null)
                focus = controller.target;
        }
        Frame();
    }

    void Update()
    {
        if (Input.GetKeyDown(flipKey))
        {
            viewFromPositiveZ = !viewFromPositiveZ;
            Frame();
        }
    }

    public void Frame()
    {
        if (focus == null)
            return;

        Bounds bounds = focus.bounds;
        float radius = Mathf.Max(bounds.extents.y, bounds.extents.x);
        float halfFov = cam.fieldOfView * 0.5f * Mathf.Deg2Rad;
        float distance = radius / (Mathf.Tan(halfFov) * fill);

        Vector3 dir = viewFromPositiveZ ? Vector3.forward : Vector3.back;
        transform.position = bounds.center + dir * distance;
        transform.rotation = Quaternion.LookRotation(-dir, Vector3.up);
        cam.nearClipPlane = Mathf.Max(0.01f, distance * 0.05f);
    }
}
