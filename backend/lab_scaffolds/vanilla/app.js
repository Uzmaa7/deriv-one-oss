const APP_ID = "{{APP_ID}}";

async function runPython(functionName, payload = {}) {
  const res = await fetch(`/api/apps/${APP_ID}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ function: functionName, payload }),
  });
  if (!res.ok) throw new Error(`run failed: ${res.status}`);
  return res.json();
}

document.getElementById("app").textContent = "{{APP_NAME}}";
