using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Text;
using System.Threading;
using UnityEngine;

/// <summary>
/// Small local HTTP API so ChatUI (and through it the LLM) can drive the
/// avatar: list and play motion sets, and send lip sync mouth shapes.
/// Requests arrive on a background thread and are run on Unity's main thread.
///
/// Endpoints (JSON in and out, default http://localhost:7870):
///   GET  /api/status                   current motion set, animation, speaking
///   GET  /api/motionsets               names and descriptions, for the LLM prompt
///   POST /api/motionset  {"name":"happy"}   (or GET /api/motionset?name=happy)
///   GET  /api/visemes                  lip sync mouth shape names
///   POST /api/lipsync    {"viseme":"A","amount":0.8}
///                        {"visemes":[{"viseme":"A","amount":0.5},{"viseme":"O","amount":0.3}]}
///   POST /api/lipsync/clear
///   GET  /api/animations, GET /api/shapekeys   what's available, for setting up sets
/// </summary>
public class AvatarApiServer : MonoBehaviour
{
    public int port = 7870;

    [Tooltip("Accept connections from other computers on the network, not just this one (e.g. if ChatUI runs on another PC). Windows Firewall will ask to allow it.")]
    public bool allowRemote;

    [Tooltip("Seconds to wait for the main thread before a request times out.")]
    public float timeout = 5f;

    HttpListener listener;
    Thread thread;
    readonly ConcurrentQueue<Action> mainThreadWork = new ConcurrentQueue<Action>();

    MotionSetLibrary library;
    AvatarAnimationPlayer player;
    LipSyncController lipSync;
    BlendShapeKeyController face;

    void Start()
    {
        library = FindObjectOfType<MotionSetLibrary>();
        player = FindObjectOfType<AvatarAnimationPlayer>();
        lipSync = FindObjectOfType<LipSyncController>();
        face = FindObjectOfType<BlendShapeKeyController>();
        StartServer();
    }

    void OnDestroy()
    {
        StopServer();
    }

    void OnApplicationQuit()
    {
        StopServer();
    }

    void Update()
    {
        while (mainThreadWork.TryDequeue(out Action work))
            work();
    }

    void StartServer()
    {
        try
        {
            listener = new HttpListener();
            listener.Prefixes.Add(allowRemote ? $"http://+:{port}/" : $"http://localhost:{port}/");
            listener.Start();
        }
        catch (Exception e)
        {
            Debug.LogWarning($"AvatarApiServer: could not listen on port {port}: {e.Message}");
            listener = null;
            return;
        }

        thread = new Thread(Listen) { IsBackground = true, Name = "AvatarApiServer" };
        thread.Start();
        Debug.Log($"AvatarApiServer: listening on http://{(allowRemote ? "*" : "localhost")}:{port}/api/");
    }

    void StopServer()
    {
        if (listener == null)
            return;
        try { listener.Stop(); listener.Close(); } catch (Exception) { }
        listener = null;
    }

    void Listen()
    {
        while (listener != null && listener.IsListening)
        {
            HttpListenerContext context;
            try
            {
                context = listener.GetContext();
            }
            catch (Exception)
            {
                return; // Stopped.
            }
            ThreadPool.QueueUserWorkItem(_ => Handle(context));
        }
    }

    void Handle(HttpListenerContext context)
    {
        HttpListenerRequest request = context.Request;
        HttpListenerResponse response = context.Response;
        int status = 200;
        string body;

        try
        {
            // Lets a browser page (e.g. a web UI) call the API too.
            response.AddHeader("Access-Control-Allow-Origin", "*");
            response.AddHeader("Access-Control-Allow-Headers", "Content-Type");
            response.AddHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");

            if (request.HttpMethod == "OPTIONS")
            {
                body = "";
            }
            else
            {
                string requestBody = "";
                if (request.HasEntityBody)
                {
                    using (var reader = new StreamReader(request.InputStream, request.ContentEncoding ?? Encoding.UTF8))
                        requestBody = reader.ReadToEnd();
                }

                string path = request.Url.AbsolutePath.TrimEnd('/').ToLowerInvariant();
                string method = request.HttpMethod;
                string nameQuery = request.QueryString["name"];

                // Everything touching Unity objects runs on the main thread.
                var done = new ManualResetEventSlim();
                Result result = null;
                mainThreadWork.Enqueue(() =>
                {
                    try { result = Route(method, path, requestBody, nameQuery); }
                    catch (Exception e) { result = Error(500, e.Message); }
                    finally { done.Set(); }
                });

                if (!done.Wait(TimeSpan.FromSeconds(timeout)))
                    result = Error(503, "Avatar is not responding (paused or loading).");

                status = result.status;
                body = result.json;
            }
        }
        catch (Exception e)
        {
            status = 500;
            body = JsonUtility.ToJson(new ErrorResponse { error = e.Message });
        }

        try
        {
            byte[] bytes = Encoding.UTF8.GetBytes(body);
            response.StatusCode = status;
            response.ContentType = "application/json; charset=utf-8";
            response.ContentLength64 = bytes.Length;
            response.OutputStream.Write(bytes, 0, bytes.Length);
            response.OutputStream.Close();
        }
        catch (Exception)
        {
            // The client went away.
        }
    }

    Result Route(string method, string path, string body, string nameQuery)
    {
        switch (path)
        {
            case "/api/status":
                return Ok(new StatusResponse
                {
                    motionSet = library != null && library.Current != null ? library.Current.name : "",
                    animation = player != null && player.Current != null ? player.Current.Label : "",
                    speed = player != null ? player.CurrentSpeed : 1f,
                    speaking = lipSync != null && lipSync.Speaking,
                });

            case "/api/motionsets":
                if (library == null)
                    return Error(404, "No MotionSetLibrary in the scene.");
                return Ok(new MotionSetsResponse
                {
                    defaultMotionSet = library.defaultMotionSet,
                    motionSets = library.motionSets.Select(s => new MotionSetInfo
                    {
                        name = s.name,
                        description = s.description,
                        animation = s.animation,
                        speed = s.speed,
                        duration = s.duration,
                        shapeKeys = s.shapeKeys.Select(k => k.shapeKey).ToArray(),
                    }).ToArray(),
                });

            case "/api/motionset":
            {
                if (library == null)
                    return Error(404, "No MotionSetLibrary in the scene.");
                string name = nameQuery;
                if (string.IsNullOrEmpty(name) && method == "POST" && !string.IsNullOrEmpty(body))
                    name = JsonUtility.FromJson<MotionSetRequest>(body)?.name;
                if (string.IsNullOrEmpty(name))
                    return Error(400, "Give the motion set name, e.g. {\"name\":\"happy\"}.");
                if (!library.Play(name))
                    return Error(404, $"No motion set named '{name}'.");
                return Ok(new OkResponse { ok = true, motionSet = library.Current.name });
            }

            case "/api/visemes":
                if (lipSync == null)
                    return Error(404, "No LipSyncController in the scene.");
                return Ok(new NamesResponse { names = lipSync.visemes.Select(v => v.viseme).ToArray() });

            case "/api/lipsync":
            {
                if (lipSync == null)
                    return Error(404, "No LipSyncController in the scene.");
                if (method != "POST")
                    return Error(405, "Use POST.");
                var request = string.IsNullOrEmpty(body) ? new LipSyncRequest() : JsonUtility.FromJson<LipSyncRequest>(body);
                if (request.visemes != null && request.visemes.Length > 0)
                {
                    lipSync.SetVisemes(request.visemes.Select(v => new KeyValuePair<string, float>(v.viseme, v.amount)));
                }
                else if (!string.IsNullOrEmpty(request.viseme))
                {
                    if (!lipSync.SetViseme(request.viseme, request.amount))
                        return Error(404, $"No viseme named '{request.viseme}'.");
                }
                else
                {
                    lipSync.Clear();
                }
                return Ok(new OkResponse { ok = true });
            }

            case "/api/lipsync/clear":
                if (lipSync != null)
                    lipSync.Clear();
                return Ok(new OkResponse { ok = true });

            case "/api/animations":
                if (player == null)
                    return Error(404, "No AvatarAnimationPlayer in the scene.");
                return Ok(new NamesResponse { names = player.entries.Where(e => e.clip != null).Select(e => e.Label).ToArray() });

            case "/api/shapekeys":
                return Ok(new NamesResponse { names = face != null && face.ShapeNames != null ? face.ShapeNames : new string[0] });

            default:
                return Error(404, $"Unknown endpoint {path}.");
        }
    }

    class Result
    {
        public int status;
        public string json;
    }

    static Result Ok(object response) => new Result { status = 200, json = JsonUtility.ToJson(response) };

    static Result Error(int status, string message) =>
        new Result { status = status, json = JsonUtility.ToJson(new ErrorResponse { error = message }) };

    // JsonUtility only handles plain serializable classes, hence these.
    [Serializable] class ErrorResponse { public string error; }
    [Serializable] class OkResponse { public bool ok; public string motionSet; }
    [Serializable] class NamesResponse { public string[] names; }
    [Serializable] class MotionSetRequest { public string name; }

    [Serializable]
    class StatusResponse
    {
        public string motionSet;
        public string animation;
        public float speed;
        public bool speaking;
    }

    [Serializable]
    class MotionSetInfo
    {
        public string name;
        public string description;
        public string animation;
        public float speed;
        public float duration;
        public string[] shapeKeys;
    }

    [Serializable]
    class MotionSetsResponse
    {
        public string defaultMotionSet;
        public MotionSetInfo[] motionSets;
    }

    [Serializable] class VisemeAmount { public string viseme; public float amount = 1f; }

    [Serializable]
    class LipSyncRequest
    {
        public string viseme;
        public float amount = 1f;
        public VisemeAmount[] visemes;
    }
}
