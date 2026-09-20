const accountId = process.env.CLOUDFLARE_ACCOUNT_ID;
const token = process.env.CLOUDFLARE_API_TOKEN;

if (!/^[a-f0-9]{32}$/i.test(accountId || "") || !token) {
  throw new Error("CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are required");
}

const response = await fetch(
  `https://api.cloudflare.com/client/v4/accounts/${accountId}/workers/scripts/tag-offline-receiver/settings`,
  {headers: {Authorization: `Bearer ${token}`}},
);
const payload = await response.json();
if (!response.ok || payload.success !== true) {
  throw new Error(`Could not read tag-offline-receiver settings: ${JSON.stringify(payload.errors || [])}`);
}

const receiverBinding = (payload.result?.bindings || []).find(
  binding => binding.type === "durable_object_namespace" &&
    (binding.name === "RECEIVER" || binding.class_name === "TagReceiver"),
);
if (receiverBinding) {
  throw new Error("TagReceiver is still bound to the live Worker; do not retire the Worker yet");
}

console.log("Verified that tag-offline-receiver no longer binds the TagReceiver Durable Object.");
