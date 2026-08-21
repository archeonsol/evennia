<script lang="ts">
  import Section from "./Section.svelte";
  import { loadDetail, runAction } from "../lib/load.svelte";

  interface Job {
    id: number;
    job_id: string;
    job_type: string;
    status: string;
    attempts: number;
    max_attempts: number;
    priority: number;
    idempotency_key?: string;
    created_at?: string;
    available_at?: string;
    lease_until?: string;
    payload?: string;
    last_error?: string;
    can_requeue?: boolean;
  }

  interface Props {
    id: string;
    onClose: () => void;
    onRequeued: () => void;
  }

  const { id, onClose, onRequeued }: Props = $props();

  let job = $state<Job | null>(null);

  $effect(() => {
    loadDetail<Job>("jobs", id).then((found) => (job = found));
  });

  const stamp = (value?: string) => (value || "--").replace("T", " ").slice(0, 19);

  const facts = $derived(
    job
      ? ([
          ["job", job.job_id],
          ["type", job.job_type],
          ["status", job.status],
          ["attempts", `${job.attempts} of ${job.max_attempts}`],
          ["priority", job.priority],
          ["idempotency key", job.idempotency_key || "--"],
          ["created", stamp(job.created_at)],
          ["available at", stamp(job.available_at)],
          ["lease until", stamp(job.lease_until)],
        ] as [string, unknown][])
      : [],
  );

  async function requeue() {
    if (!job) return;
    const reason = prompt("Why is this job being requeued?");
    if (!reason) return;
    if ((await runAction("jobs", "requeue", { job_id: job.id, reason })) !== null) onRequeued();
  }
</script>

<div class="detail">
  <div class="toolbar">
    <span class="legend">JOB</span>
    <span class="spacer"></span>
    <button type="button" onclick={onClose}>CLOSE</button>
  </div>

  {#if job}
    <dl class="rows">
      {#each facts as [label, value] (label)}
        <div class="row-pair"><dt>{label}</dt><dd>{value ?? ""}</dd></div>
      {/each}
    </dl>

    <Section label="Payload" />
    <pre class="code">{job.payload || ""}</pre>

    {#if job.last_error}
      <Section label="Last error" />
      <pre class="code">{job.last_error}</pre>
    {/if}

    {#if job.can_requeue}
      <div class="fault-actions">
        <button
          type="button"
          title="Return this job to the queue. The next queue run starts it."
          onclick={requeue}
        >
          REQUEUE
        </button>
      </div>
    {:else}
      <!-- Said, not left to be inferred from a missing button. -->
      <p class="empty-hint">
        You can requeue only a job with the status 'dead'. This job has the status {job.status}.
      </p>
    {/if}
  {/if}
</div>
