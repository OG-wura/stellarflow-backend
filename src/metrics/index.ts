import { Counter, Histogram } from "prom-client";

export const successfulSubmissions = new Counter({
  name: "multi_sig_successful_submissions_total",
  help: "Total number of successfully submitted multi-signature updates",
  labelNames: ["asset"],
});

export const failedSubmissions = new Counter({
  name: "multi_sig_failed_submissions_total",
  help: "Total number of failed multi-signature submissions",
  labelNames: ["asset", "reason"],
});

export const gasUsagePerAsset = new Histogram({
  name: "multi_sig_gas_usage_stroops",
  help: "Gas usage per asset for multi-signature submissions",
  labelNames: ["asset"],
});

export const submissionDuration = new Histogram({
  name: "multi_sig_submission_duration_seconds",
  help: "Duration of multi-signature submission processing",
  labelNames: ["asset"],
});
