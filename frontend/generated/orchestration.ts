import type { components } from "./api.generated";

export type OrchestrationSnapshotResponse =
  components["schemas"]["OrchestrationSnapshotResponse"];
export type OrchestrationStatusPayload =
  components["schemas"]["OrchestrationStatusPayload"];
export type OrchestrationDonePayload =
  components["schemas"]["OrchestrationDonePayload"];
export type OrchestrationProgressPayload =
  components["schemas"]["OrchestrationProgressPayload"];
export type OrchestrationResultPayload =
  components["schemas"]["OrchestrationResultPayload"];
export type OrchestrationTracePayload =
  components["schemas"]["OrchestrationTracePayload"];
export type CaptureGuidancePayload =
  components["schemas"]["CaptureGuidancePayload"];

export interface OrchestrationTokenPayload {
  text?: string;
}
