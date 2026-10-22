# ComfyUI workflows

Each `.json` here is one workflow a chat can pick in **Chat Settings > Pictures**.
The file name without `.json` is what the chat shows and saves.

## Adding a workflow

1. Build and test the workflow in ComfyUI as usual.
2. In the two prompt boxes, type `{{positive}}` and `{{negative}}` where the
   model's prompts should go. Fixed words around them are kept, e.g.
   `masterpiece, best quality, {{positive}}`.
   (Without the placeholders the server follows the sampler's positive and
   negative inputs back to their text boxes and fills those, which works for
   the usual SD 1.5, SDXL and Flux graphs.)
3. **Workflow > Export (API)** and save the file here. The normal *Save* format
   (the one with a `"nodes"` list) can't be queued over the API; the chat's
   workflow list marks such files as unusable.

Seeds (`seed`, `noise_seed`) are randomised on every picture.

`sd15-basic.json` is ComfyUI's default text-to-image graph with placeholders.
It needs `v1-5-pruned-emaonly.safetensors` in `ComfyUI/models/checkpoints`;
change `ckpt_name` (node 4) to a checkpoint you have.

A different folder can be set with `COMFYUI_WORKFLOW_DIR` in `Server/ollama/.env`.
