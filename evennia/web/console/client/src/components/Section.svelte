<script lang="ts">
  interface Props {
    label: string;
  }

  const { label }: Props = $props();
</script>

<p class="section-legend">{label}</p>
