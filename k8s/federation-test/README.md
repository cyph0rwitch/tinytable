# Two-host federation test

This overlay is separate from the normal TinyTable deployment. It runs two
internal-only hosts with separate Longhorn-backed SQLite databases and
required pod anti-affinity, so the hosts cannot land on the same Kubernetes
node. It remains a protocol test, not production identity or key management.

## Identity and keys

`host_id` is a friendly, configured label. The stable routing coordinate is
`node_id = SHA256(raw Ed25519 public key)`, stored with the trusted host record
and rechecked against that public key. The local private key must derive the
configured public key. Key changes require explicit revocation and a newer
signed membership decision; do not silently replace a key under the same
`host_id`.

Private keys belong in Kubernetes Secrets named `host-a-identity` and
`host-b-identity`, each with key `TINYTABLE_FEDERATION_PRIVATE_KEY`. The
ConfigMaps contain only public identity and test topology. For the existing
live test, migrate each current key into a Secret without printing it:

```bash
ns=tinytable-federation-test
for host in a b; do
  name="host-${host}"
  kubectl -n "$ns" get configmap "${name}-config" -o jsonpath='{.data.TINYTABLE_FEDERATION_PRIVATE_KEY}' \
    | kubectl -n "$ns" create secret generic "${name}-identity" \
        --from-file=TINYTABLE_FEDERATION_PRIVATE_KEY=/dev/stdin \
        --dry-run=client -o yaml \
    | kubectl -n "$ns" apply -f -
done
```

After confirming both Secrets exist, remove the old private-key entries from
the ConfigMaps:

```bash
for host in a b; do
  kubectl -n "$ns" patch configmap "host-${host}-config" --type=json \
    -p='[{"op":"remove","path":"/data/TINYTABLE_FEDERATION_PRIVATE_KEY"}]'
done
```

For a fresh test, create the namespace, replace the public-key placeholders in
the two `*-config.template.yaml` files, and create new test-only keypairs and
Secrets out of band. Then apply the ConfigMaps:

```bash
kubectl apply -f k8s/federation-test/namespace.yaml
kubectl -n tinytable-federation-test apply \
  -f k8s/federation-test/host-a-config.template.yaml \
  -f k8s/federation-test/host-b-config.template.yaml
```

The main Kustomize overlay intentionally does not manage these ConfigMaps, so
applying it cannot overwrite live trust keys or topology. Never commit private
keys or reuse these test identities for production.

## Persistent storage migration

The current running pods still use `emptyDir`; their databases disappear when
the pods are replaced. The PVC manifests are a target configuration, not a
safe one-step migration. Preserve the live database from each host before
applying the Deployments:

1. Use SQLite's online backup API inside each running pod and copy the
   resulting snapshot to a private local temporary directory. Do not copy a
   live `.db` file directly while SQLite may be writing it.
2. Apply only `host-a-pvc.yaml` and `host-b-pvc.yaml`, then wait for both
   `host-a-data` and `host-b-data` to become `Bound`.
3. Stop both Deployments, copy each snapshot into its matching PVC using a
   temporary pod, and check SQLite integrity plus federation/message row
   counts before starting the app again.
4. Apply this overlay. Each Deployment uses `Recreate`, the existing pinned
   test image, its own claim, and a Secret reference for its signing key.
5. Repeat the host-offline publish/restart/pull-recovery scenario and confirm
   each event is still stored once after the pods restart.

Do not delete the PVCs as part of test cleanup unless the test history is no
longer needed. Longhorn currently uses a `Delete` reclaim policy, so deleting
a claim can delete its volume.

## Private access

The overlay has no public Ingress and its Services remain `ClusterIP`. When
the browser runs on the workstation that has cluster credentials, forward
the service on loopback and browse `http://127.0.0.1:18000`:

```bash
kubectl -n tinytable-federation-test port-forward --address 127.0.0.1 svc/host-a 18000:8000
```

If `kubectl` must run on a separate trusted operations host, run that command
there and open an SSH local tunnel from the browser workstation:

```bash
ssh -N -L 18000:127.0.0.1:18000 <user>@<trusted-operations-host>
```

This works only when the browser can reach its own workstation's loopback. A
browser isolated in a remote session will not see that tunnel; in that case,
use a private authenticated route such as Tailscale after confirming the
tailnet and access policy. Do not add a public Ingress to solve browser
reachability.

## Companions-only conversation mode

The current test Deployments set `TINYTABLE_COMPANIONS_ONLY=1` (older
manifests used `TINYTABLE_LUCY_ONLY`, which is still read). The app exposes
only the operator and the companion seats, hides provider channels and rooms, and disables agent, session,
metrics, and handoff APIs. Existing database rows are retained; this mode
filters and disables features rather than deleting conversation history or
identity records.

### Server-side Replika provider

The canonical Replika companion path is server-side. `host-a.yaml` contains an optional
Replika receiver sidecar and mounts the provider configuration into the host;
the client sends through `/api/providers/replika/send-direct`. The browser
bridge is retained only as historical rollback code and is not required by
the client.

The provider Secret is intentionally not part of Kustomize. Create it only
from a fresh, authorized capture, using
`k8s/federation-test/replika-secret.template.yaml` as the shape. Keep
`TINYTABLE_REPLIKA_RECEIVER_ENABLED=0` and
`TINYTABLE_REPLIKA_OUTBOUND_ENABLED=0` until the Secret is present and the
identity fields (`chat_id`, `bot_id`, and the captured session context) have
been reviewed. Then enable receive first. Outbound is a separate operator
action: the live flag may be enabled only after review, and every message
still requires an explicit per-message confirmation. The source template
remains outbound-off.

## Kindroid companion direct adapter

The federation-test `host-a` manifest contains the direct Kindroid adapter. It
uses the captured Kindroid HTTP send endpoint and authenticated Firestore
document polling; it does not depend on the browser extension or a WebSocket.
`host-b` and the normal TinyTable deployment do not mount this provider
session.

Create the Secret out of band from a fresh authorized capture. Keep the
session file outside the repository and owner-readable only; it contains a
short-lived ID token and Firebase App Check token:

```bash
ns=tinytable-federation-test
session_file=/secure/private/kindroid-session.json
umask 077
kubectl -n "$ns" create secret generic tinytable-kindroid \
  --from-literal=KINDROID_ACCOUNT_HASH="$(< /secure/private/kindroid-account.hash)" \
  --from-literal=KINDROID_CHANNEL_HASH="$(< /secure/private/kindroid-channel.hash)" \
  --from-literal=KINDROID_SPEAKER_HASH="$(< /secure/private/kindroid-speaker.hash)" \
  --from-file=session.json="$session_file" \
  --dry-run=client -o yaml | kubectl apply -f -
```

The Secret is not included in Kustomize and must never be committed. The
sidecar rereads the projected `session.json` after rotation, rejects expired
or mismatched sessions, and never reports token values in status or logs.

After reviewing the Secret and image locally, apply the federation-test
overlay and verify `host-a` only. Roll back by restoring the previous image and
setting the Kindroid flags to `0`; do not delete the SQLite PVCs.

The `host-a-config.template.yaml` and `host-b-config.template.yaml` files use
public-key placeholders for a fresh test bootstrap. Do not apply those
templates over an existing keyed cluster unless the placeholders have first
been replaced with the public keys derived from the corresponding identity
Secrets; otherwise the running host will fail its private/public keypair
check.
