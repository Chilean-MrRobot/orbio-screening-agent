"""Componente HTML de la llamada (spec 022): SDK oficial de ElevenLabs desde CDN, versión fija.

Recibe solo el token de un uso y las variables dinámicas; la key de ElevenLabs no pasa por aquí.
"""

from __future__ import annotations

import json

from screening.api.voice_call import CallSession

SDK_URL = "https://unpkg.com/@elevenlabs/client@1.28.0/dist/lib.iife.js"
HEIGHT = 560

_TEMPLATE = """
<style>
  :root { --bg:#ffffff; --fg:#1f2328; --muted:#656d76; --line:#d0d7de; --accent:#e4572e;
          --agent:#f3f4f6; --user:#fde8e1; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0e1117; --fg:#e6edf3; --muted:#9198a1; --line:#30363d; --accent:#ff7a59;
            --agent:#1c2128; --user:#3a2620; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font-family: "Source Sans Pro", system-ui, sans-serif; }
  .bar { display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
  button { border:0; border-radius:999px; padding:10px 20px; font-size:16px; cursor:pointer;
           color:#fff; background:var(--accent); }
  button.hang { background:#c62828; }
  button:disabled { opacity:.5; cursor:default; }
  .status { color:var(--muted); font-size:14px; }
  .dot { display:inline-block; width:10px; height:10px; border-radius:50%;
         background:var(--muted); margin-right:6px; }
  .dot.listening { background:#2e7d32; }
  .dot.speaking { background:var(--accent); animation:pulse 1s infinite; }
  @keyframes pulse { 50% { opacity:.3; } }
  #log { margin-top:12px; height:400px; overflow-y:auto; border:1px solid var(--line);
         border-radius:8px; padding:10px; }
  .msg { padding:8px 12px; border-radius:10px; margin:6px 0; max-width:85%;
         line-height:1.35; white-space:pre-wrap; }
  .agent { background:var(--agent); }
  .user { background:var(--user); margin-left:auto; }
  .who { font-size:12px; color:var(--muted); margin-bottom:2px; }
  form { display:flex; gap:8px; margin-top:8px; }
  input { flex:1; padding:8px 12px; border-radius:8px; border:1px solid var(--line);
          background:var(--bg); color:var(--fg); font-size:15px; }
  form button { padding:8px 14px; font-size:14px; }
</style>
<div class="bar">
  <button id="call">📞 Llamar a Sofía</button>
  <button id="hang" class="hang" disabled>Colgar</button>
  <span class="status"><span id="dot" class="dot"></span>
    <span id="status">Lista para llamar</span></span>
</div>
<div id="log"></div>
<form id="typed"><input id="text" placeholder="¿Sin micrófono? Escribe aquí durante la llamada"
  disabled><button id="send" disabled>Enviar</button></form>
<script src="__SDK_URL__"></script>
<script>
const cfg = __CONFIG__;
let conversation = null;
const $ = (id) => document.getElementById(id);
function setStatus(text, mode) {
  $("status").textContent = text;
  $("dot").className = "dot " + (mode || "");
}
function add(role, text) {
  const div = document.createElement("div");
  div.className = "msg " + (role === "user" ? "user" : "agent");
  div.innerHTML = '<div class="who"></div>';
  div.firstChild.textContent = role === "user" ? "Tú" : "Sofía";
  div.appendChild(document.createTextNode(text));
  $("log").appendChild(div);
  $("log").scrollTop = $("log").scrollHeight;
}
function connected(on) {
  $("call").disabled = on;
  for (const id of ["hang", "text", "send"]) $(id).disabled = !on;
}
$("call").onclick = async () => {
  if (cfg.used) {
    setStatus("Para volver a llamar, pulsa «Nueva llamada» a la izquierda.");
    return;
  }
  setStatus("Pidiendo el micrófono…");
  try {
    // Solo para pedir el permiso: se suelta enseguida. Si no, la llamada corre con dos
    // capturas del micrófono abiertas (el SDK abre la suya).
    const probe = await navigator.mediaDevices.getUserMedia({ audio: true });
    probe.getTracks().forEach((track) => track.stop());
  } catch (e) {
    // El token sigue sin usar: se puede reintentar después de permitir el micrófono.
    setStatus("Sin micrófono no se puede llamar. Permítelo en el navegador o usa «Postular».");
    return;
  }
  cfg.used = true;
  $("call").disabled = true;
  setStatus("Conectando…");
  try {
    const options = {
      conversationToken: cfg.token,
      connectionType: "webrtc",
      dynamicVariables: cfg.variables,
      onConnect: () => { connected(true); setStatus("Conectada", "listening"); },
      onDisconnect: () => {
        connected(false);
        setStatus("Llamada terminada. Mira la ficha en el Panel.");
      },
      onMessage: (m) => add(m.role || (m.source === "user" ? "user" : "agent"), m.message),
      onModeChange: ({ mode }) =>
        setStatus(mode === "speaking" ? "Sofía está hablando" : "Te escucho…", mode),
      onError: (message) => setStatus("Error: " + message),
    };
    if (cfg.language && cfg.language !== "es") {
      options.overrides = { agent: { language: cfg.language } };
    }
    conversation = await ElevenLabsClient.Conversation.startSession(options);
  } catch (e) {
    connected(false);
    setStatus("No se pudo conectar: " + (e && e.message ? e.message : e));
  }
};
$("hang").onclick = async () => { if (conversation) await conversation.endSession(); };
$("typed").onsubmit = (event) => {
  event.preventDefault();
  const text = $("text").value.trim();
  if (!text || !conversation) return;
  conversation.sendUserMessage(text);
  add("user", text);
  $("text").value = "";
};
</script>
"""


def widget_html(session: CallSession) -> str:
    config = {
        "token": session.conversation_token,
        "variables": session.dynamic_variables,
        "language": session.language,
        "used": False,
    }
    # `</` escapado: el JSON va dentro de un <script> y no debe poder cerrarlo.
    payload = json.dumps(config, ensure_ascii=False).replace("</", "<\\/")
    return _TEMPLATE.replace("__SDK_URL__", SDK_URL).replace("__CONFIG__", payload)
