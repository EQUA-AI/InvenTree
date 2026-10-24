/**
 * Types for `twinScene.mjs`, the R6 authored reference pump scene
 * (EQUA_AIMMS_Handoff_R6_FINAL_UI_UX/Existing_3D_Reference/twin-scene.mjs).
 * The native adapter adds theme, interaction and mimic alarm support;
 * these declarations describe its public controller API.
 */

export type TwinPartId =
  | 'machine'
  | 'motor'
  | 'bearing'
  | 'coupling'
  | 'seal'
  | 'impeller'
  | 'pump'
  | 'discharge'
  | 'base';

export type TwinMode = 'assembled' | 'cutaway' | 'exploded';
export type TwinView = 'iso' | 'front' | 'side' | 'top';

export interface TwinMotorAlarm {
  severity: 'critical' | 'warning';
  attention?: 'unacknowledged' | 'acknowledged';
  label?: string;
}

export interface TwinPart {
  readonly id: TwinPartId;
  readonly number: number;
  readonly label: string;
  readonly parent: TwinPartId | null;
  readonly description: string;
  /** Always true: authored conceptual geometry, not verified equipment. */
  readonly conceptual: true;
}

export interface TwinCameraState {
  position: [number, number, number];
  target: [number, number, number];
}

export interface TwinState {
  ready: boolean;
  selected: TwinPartId;
  mode: TwinMode;
  labels: boolean;
  conceptual: true;
  cameraState: TwinCameraState | null;
  error: string | null;
}

export interface TwinReady {
  version: string;
  renderer: string;
  conceptual: true;
  parts: readonly TwinPart[];
  mode: TwinMode;
  selected: TwinPartId;
}

export type TwinError = Error & {
  code?: 'INITIALIZATION_FAILED' | 'CONTEXT_LOST' | 'RENDER_FAILED' | string;
};

export interface TwinOptions {
  selected?: TwinPartId;
  mode?: TwinMode;
  labels?: boolean;
  motorAlarm?: TwinMotorAlarm | null;
  cameraState?: TwinCameraState;
  hotspots?: boolean;
  onOccluded?: (parts: {id: TwinPartId; number: number; label: string}[]) => void;
  onSelect?: (id: TwinPartId, part: TwinPart | undefined) => void;
  onReady?: (ready: TwinReady) => void;
  onError?: (error: TwinError) => void;
}

export interface TwinController {
  destroy(): void;
  setTheme(): void;
  setRotate(value: boolean): void;
  select(id: TwinPartId): void;
  setMode(mode: TwinMode): void;
  setLabels(value: boolean): void;
  setMotorAlarm(value: TwinMotorAlarm | null): void;
  setView(view: TwinView): void;
  zoom(factor: number): void;
  focus(id: TwinPartId): void;
  reset(): void;
  getState(): TwinState;
  readonly parts: readonly TwinPart[];
  readonly metadata: {
    readonly conceptual: true;
    readonly renderer: string;
    readonly version: string;
  };
}

export declare const PARTS: readonly TwinPart[];
export declare const VERSION: string;
export declare function create(
  host: HTMLElement,
  options?: TwinOptions
): TwinController;
