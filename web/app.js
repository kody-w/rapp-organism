"use strict";

const element = id => document.getElementById(id);
const words = text => String(text).replaceAll("_", " ");
const number = value => new Intl.NumberFormat("en").format(value);
const textNode = (tag, text) => {
  const node = document.createElement(tag);
  node.textContent = String(text);
  return node;
};

function publicLink(label, href, allowedHosts = ["github.com", "lite.datasette.io"]) {
  const url = new URL(href);
  if (url.protocol !== "https:" || !allowedHosts.includes(url.hostname) || url.username || url.password) {
    throw new Error("Unsupported public link");
  }
  const link = textNode("a", label);
  link.href = url.href;
  return link;
}

async function getJSON(path) {
  const response = await fetch(path, {cache: "no-cache"});
  if (!response.ok) throw new Error("Public metadata unavailable");
  return response.json();
}

function showFreshness(receipt) {
  const last = receipt.last_success_at ? Date.parse(receipt.last_success_at) : NaN;
  const age = (Date.now() - last) / 1000;
  let freshness = "unknown";
  if (Number.isFinite(age) && age >= 0) freshness = age > receipt.stale_after_seconds ? "stale" : "fresh";
  const poll = receipt.status === "success" ? "last published poll succeeded" :
    receipt.status === "failed" ? "last published poll failed" : "no successful live poll recorded";
  element("freshness").textContent = `Source freshness: ${freshness} — ${poll}.`;
  element("freshness-detail").textContent = receipt.last_success_at ?
    `Last successful complete metadata poll: ${receipt.last_success_at}. Unchanged metadata may reuse head probes for at most 24 hours; this is not a runtime check.` :
    "Only frozen capture metadata is available. A successful build does not establish today's public heads or carrier availability.";
}

function showRepositories(rows) {
  const pageSize = 50;
  let page = 0;
  function render() {
    const search = element("search").value.trim().toLocaleLowerCase();
    const scope = element("scope").value;
    const filtered = rows.filter(row => (scope === "all" || row.scope === scope) &&
      row.full_name.toLocaleLowerCase().includes(search));
    page = Math.min(page, Math.max(0, Math.ceil(filtered.length / pageSize) - 1));
    const visible = filtered.slice(page * pageSize, (page + 1) * pageSize);
    const body = element("repo-rows");
    body.replaceChildren();
    for (const row of visible) {
      const tr = document.createElement("tr");
      const name = document.createElement("td");
      name.append(publicLink(row.full_name, row.public_url));
      tr.append(name);
      const commit = document.createElement("td");
      if (row.captured_commit) {
        const link = publicLink(row.captured_commit.slice(0, 12), `${row.public_url}/tree/${row.captured_commit}`);
        link.title = row.captured_commit;
        commit.append(link);
      } else {
        commit.textContent = "empty at capture";
      }
      tr.append(commit, textNode("td", number(row.tracked_entries)));
      const observation = row.observation;
      const status = textNode("td", observation ? words(observation.availability) : "not polled");
      if (observation && observation.observed_head) {
        status.append(textNode("br", ""), textNode("code", observation.observed_head.slice(0, 12)));
        status.title = observation.availability === "not_listed_public" ?
          "Last-known head only; this repository was not in the complete public listing." : observation.observed_head;
      }
      tr.append(status, textNode("td", row.license_spdx || "unknown; inspect component"));
      body.append(tr);
    }
    const start = filtered.length ? page * pageSize + 1 : 0;
    element("result-count").textContent =
      `Showing ${number(start)}–${number(page * pageSize + visible.length)} of ${number(filtered.length)} matching records. JSON/CSV contains every captured record.`;
    element("previous").disabled = page === 0;
    element("next").disabled = (page + 1) * pageSize >= filtered.length;
  }
  element("search").addEventListener("input", () => { page = 0; render(); });
  element("scope").addEventListener("change", () => { page = 0; render(); });
  element("previous").addEventListener("click", () => { page -= 1; render(); });
  element("next").addEventListener("click", () => { page += 1; render(); });
  render();
}

async function start() {
  const [index, repos, receipt, authority, distribution, workflows] = await Promise.all([
    getJSON("index.json"), getJSON("repos.json"),
    getJSON("freshness.json"), getJSON("data/authority.json"), getJSON("data/distribution.json"),
    getJSON("workflow-observations.json"),
  ]);
  const latest = index.generations[index.generations.length - 1];
  const counters = [
    ["Owner repositories", latest.owner_repos], ["Owner paths", latest.owner_files],
    ["Dependency paths", latest.dependency_files], ["Unique blobs", latest.blobs],
    ["Unavailable gitlinks", latest.unavailable_gitlinks],
  ];
  for (const [label, value] of counters) {
    const card = document.createElement("div");
    card.append(textNode("dt", label), textNode("dd", number(value)));
    element("counts").append(card);
  }
  element("capture-note").textContent =
    `Generation ${latest.generation_id} · captured ${latest.captured_at} · complete frozen-scope metadata only · raw payload ${words(index.payload_availability)} · runtime compatibility not established.`;
  element("db-size").textContent = `(${(index.database.bytes / 1048576).toFixed(1)} MiB)`;
  showFreshness(receipt);
  setInterval(() => showFreshness(receipt), 60000);
  showRepositories(repos.rows);
  for (const observation of workflows.rows) {
    const item = document.createElement("li");
    const measured = observation.measured;
    item.append(textNode("strong", `${observation.observed_date}: reported DOGG → RAPP1 → Node byte recovery`));
    item.append(textNode("p",
      `${measured.producer} → ${measured.receiver}; ${measured.emitted_frames_verified} emitted frame verified and all ${observation.source.bytes} source bytes recovered. ` +
      `Fresh public checkout, ${measured.source_repairs} source repairs, no private bootstrap; ${measured.bridge_contracts_passed} bridge contracts passed. ` +
      `${measured.native_frames_checked} native frames and ${measured.native_chains_checked} chains checked.`));
    item.append(textNode("p",
      "Scope: unsigned local snapshot integrity/recovery, separate runtimes and directories on one physical machine. " +
      "No authenticated consumer acceptance, OS-enforced sandbox, recovered-code execution, new protocol ratification, or full-organism runtime compatibility is established."));
    item.append(publicLink("Historical public starting point", observation.public_starting_point), " · ",
      publicLink("Pinned source commit", `${observation.source.repository}/tree/${observation.source.commit}`), " · ",
      publicLink("Unmerged reference candidate", `${observation.reference_candidate.repository}/tree/${observation.reference_candidate.commit}`));
    const integration = workflows.source_integrations.find(row => row.observation_id === observation.observation_id);
    if (integration) {
      item.append(textNode("p",
        `Source integration: DOGG PR #${integration.pull_request_number} merged into ${integration.target_branch} at ${integration.merge_commit}. ` +
        `The measured exercise remains pinned to ${integration.tested_source_commit}. This DOGG merge is not RAPP1 reference-protocol ratification.`));
      item.append(publicLink("DOGG merge record", integration.pull_request_url), " · ",
        publicLink("DOGG merge checkpoint", `${integration.repository}/tree/${integration.merge_commit}`), " · ",
        publicLink("DOGG main", `${integration.repository}/tree/${encodeURIComponent(integration.target_branch)}`));
    }
    element("workflow-list").append(item);
  }
  const accepted = authority.accepted;
  const content = authority.content_source;
  const candidate = authority.candidate;
  element("authority-status").textContent =
    `Accepted canonical-main checkpoint: ${accepted.commit}. ` +
    `${content.revision} normative content source: ${content.commit}; SPEC SHA-256: ${content.spec_sha256}. ` +
    `Reviewed implementation candidate: PR #${candidate.pull_request_number} at ${candidate.commit}. ` +
    "These are distinct roles. Review/check success is not merger or ratification; runtime activation is not established.";
  if (accepted.repository_url) {
    element("authority-status").append(" ", publicLink("Accepted source", `${accepted.repository_url}/tree/${accepted.commit}`));
  }
  const specPath = content.spec_path.split("/").map(encodeURIComponent).join("/");
  element("authority-status").append(" · ", publicLink("Normative SPEC", `${content.repository_url}/blob/${content.commit}/${specPath}`));
  if (candidate.pull_request_url) {
    element("authority-status").append(" · ", publicLink("Reviewed proposal", candidate.pull_request_url));
  }
  const carrier = distribution.generations.find(row => row.generation_id === latest.generation_id);
  if (carrier) {
    const availability = carrier.publication_status === "withheld" ? "WITHHELD" : words(carrier.publication_status);
    element("carrier-status").textContent = `Exact full carrier: ${availability}`;
    element("carrier-detail").textContent = carrier.publication_status === "withheld" ?
      `Clear for exact publication: false. This allowlisted metadata/query projection is projection_of SHA-256 ${carrier.carrier_sha256} (${number(carrier.carrier_bytes)} original bytes). ` +
      "The raw/private payload is unavailable here. No raw or encrypted-full upload/download is offered. Complete metadata is not payload clearance." :
      `${number(carrier.carrier_bytes)} bytes · screening: ${words(carrier.screening_status)} · clear for exact publication: ${carrier.clear_for_exact_publication}. ` +
      `Carrier SHA-256: ${carrier.carrier_sha256}. Integrity does not establish runtime activation.`;
    if (carrier.publication_status === "published") {
      for (const part of carrier.parts) {
        const item = document.createElement("li");
        item.append(publicLink(`Verified-manifest part ${part.index} (${number(part.bytes)} bytes)`, part.url));
        element("carrier-parts").append(item);
      }
    }
  }
  element("load-status").textContent = "Public metadata loaded. No source code was executed.";
}

start().catch(() => {
  element("load-status").textContent =
    "Could not load a complete catalog. No partial result is presented as current; try the JSON/CSV links or check the latest Pages deployment.";
});
