using System;
using System.Collections;
using System.Runtime.InteropServices;
using UnityEngine;

/// <summary>
/// Turns a Windows build into a desktop companion: the window becomes
/// borderless, transparent, always on top and as big as the screen, and
/// mouse clicks pass through to the desktop everywhere except on the avatar
/// and the on-screen panels.
///
/// Only takes effect in a Windows standalone build; in the editor it does
/// nothing, so the scene keeps its normal background. Build with
/// ChatUI > Build Desktop Companion, which also sets the player settings
/// this needs (DXGI flip model off, windowed, run in background).
///
/// In the companion: right-drag her to move her, scroll over her to resize,
/// Tab hides or shows the panels, Ctrl+Q quits.
/// </summary>
[RequireComponent(typeof(Camera))]
public class DesktopCompanionWindow : MonoBehaviour
{
    [Tooltip("How much of the screen height she fills at start.")]
    [Range(0.1f, 1f)] public float startFill = 0.45f;

    [Tooltip("Pixels with at least this alpha count as part of her (or a panel) and catch the mouse.")]
    [Range(0.01f, 1f)] public float hitAlpha = 0.1f;

    public Vector2 scaleRange = new Vector2(0.3f, 3f);

    public KeyCode togglePanelsKey = KeyCode.Tab;

    Camera cam;
    Transform avatarRoot;
    Texture2D pixel;
    bool overAvatar;
    bool dragging;
    Vector3 lastDragWorld;

    void Start()
    {
        cam = GetComponent<Camera>();
        var face = FindObjectOfType<BlendShapeKeyController>();
        if (face != null && face.target != null)
            avatarRoot = face.target.transform.root;

#if UNITY_STANDALONE_WIN && !UNITY_EDITOR
        cam.clearFlags = CameraClearFlags.SolidColor;
        cam.backgroundColor = new Color(0f, 0f, 0f, 0f);
        cam.allowHDR = false;

        var framer = GetComponent<AvatarCameraFramer>();
        if (framer != null)
            framer.Reframe(true, startFill);

        pixel = new Texture2D(1, 1, TextureFormat.RGBA32, false);
        MakeOverlayWindow();
        StartCoroutine(HitTestLoop());
#else
        Debug.Log("DesktopCompanionWindow: transparent desktop mode only applies in a Windows build.");
#endif
    }

    void Update()
    {
        if (Input.GetKey(KeyCode.LeftControl) && Input.GetKeyDown(KeyCode.Q))
            Application.Quit();

#if UNITY_STANDALONE_WIN && !UNITY_EDITOR
        if (!BlendShapeKeyController.TypingInUI && Input.GetKeyDown(togglePanelsKey))
            BlendShapeKeyController.PanelsVisible = !BlendShapeKeyController.PanelsVisible;

        HandleDragAndScale();
#endif
    }

    void HandleDragAndScale()
    {
        if (avatarRoot == null)
            return;

        if (Input.GetMouseButtonDown(1) && overAvatar)
        {
            dragging = true;
            lastDragWorld = MouseOnAvatarPlane();
        }
        if (Input.GetMouseButtonUp(1))
            dragging = false;

        if (dragging)
        {
            Vector3 now = MouseOnAvatarPlane();
            avatarRoot.position += now - lastDragWorld;
            lastDragWorld = now;
        }

        float wheel = Input.mouseScrollDelta.y;
        if (overAvatar && Mathf.Abs(wheel) > 0.01f)
        {
            float scale = Mathf.Clamp(avatarRoot.localScale.x * (1f + wheel * 0.1f), scaleRange.x, scaleRange.y);
            avatarRoot.localScale = Vector3.one * scale;
        }
    }

    Vector3 MouseOnAvatarPlane()
    {
        float depth = Vector3.Dot(avatarRoot.position - cam.transform.position, cam.transform.forward);
        Vector3 mouse = Input.mousePosition;
        mouse.z = depth;
        return cam.ScreenToWorldPoint(mouse);
    }

#if UNITY_STANDALONE_WIN && !UNITY_EDITOR
    // Reads the rendered pixel under the cursor after each frame and lets
    // clicks through wherever it's transparent. The cursor comes from Win32
    // because Unity gets no mouse events while the window is click-through.
    IEnumerator HitTestLoop()
    {
        var endOfFrame = new WaitForEndOfFrame();
        while (true)
        {
            yield return endOfFrame;

            bool hit = false;
            if (GetCursorPos(out POINT p) && ScreenToClient(window, ref p))
            {
                int x = p.X;
                int y = Screen.height - 1 - p.Y;
                if (x >= 0 && y >= 0 && x < Screen.width && y < Screen.height)
                {
                    pixel.ReadPixels(new Rect(x, y, 1, 1), 0, 0, false);
                    hit = pixel.GetPixel(0, 0).a >= hitAlpha;
                }
            }

            // Keep catching the mouse for the rest of a drag, even off her.
            hit |= dragging;
            if (hit != overAvatar)
            {
                overAvatar = hit;
                SetWindowLong(window, GWL_EXSTYLE, hit ? WS_EX_LAYERED : WS_EX_LAYERED | WS_EX_TRANSPARENT);
            }
        }
    }

    IntPtr window;

    void MakeOverlayWindow()
    {
        int width = Display.main.systemWidth;
        int height = Display.main.systemHeight;
        Screen.SetResolution(width, height, FullScreenMode.Windowed);

        window = GetActiveWindow();
        SetWindowLong(window, GWL_STYLE, WS_POPUP | WS_VISIBLE);
        SetWindowLong(window, GWL_EXSTYLE, WS_EX_LAYERED | WS_EX_TRANSPARENT);

        // A negative margin extends the glass frame over the whole window, so
        // DWM composites it using the alpha Unity renders.
        var margins = new MARGINS { cxLeftWidth = -1 };
        DwmExtendFrameIntoClientArea(window, ref margins);

        SetWindowPos(window, HWND_TOPMOST, 0, 0, width, height, SWP_FRAMECHANGED | SWP_SHOWWINDOW);
    }

    [StructLayout(LayoutKind.Sequential)]
    struct POINT { public int X; public int Y; }

    [StructLayout(LayoutKind.Sequential)]
    struct MARGINS { public int cxLeftWidth, cxRightWidth, cyTopHeight, cyBottomHeight; }

    const int GWL_STYLE = -16;
    const int GWL_EXSTYLE = -20;
    const uint WS_POPUP = 0x80000000;
    const uint WS_VISIBLE = 0x10000000;
    const uint WS_EX_LAYERED = 0x00080000;
    const uint WS_EX_TRANSPARENT = 0x00000020;
    const uint SWP_FRAMECHANGED = 0x0020;
    const uint SWP_SHOWWINDOW = 0x0040;
    static readonly IntPtr HWND_TOPMOST = new IntPtr(-1);

    [DllImport("user32.dll")] static extern IntPtr GetActiveWindow();
    [DllImport("user32.dll")] static extern int SetWindowLong(IntPtr hWnd, int nIndex, uint dwNewLong);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int x, int y, int cx, int cy, uint uFlags);
    [DllImport("user32.dll")] static extern bool GetCursorPos(out POINT point);
    [DllImport("user32.dll")] static extern bool ScreenToClient(IntPtr hWnd, ref POINT point);
    [DllImport("dwmapi.dll")] static extern int DwmExtendFrameIntoClientArea(IntPtr hWnd, ref MARGINS margins);
#endif
}
