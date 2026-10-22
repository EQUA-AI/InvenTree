import type { PartState } from './MimicParts';
import type { Display } from './MimicReadings';
import { UnitSchematic } from './UnitSchematic';
import { valueText } from './format';
import { operatingState, stateStyle } from './mimicVisuals';
import type { MimicData } from './types';

export { alarmColor, alarmLabel, stateStyle } from './mimicVisuals';

/** Station uses StationSchematic; this adapter preserves the native unit route. */
export function MimicSchematic({
  data,
  unit,
  display = valueText,
  onPart
}: Readonly<{
  data: MimicData;
  unit: string;
  display?: Display;
  onPart?: (state: PartState) => void;
}>) {
  const bay = data.bays.find((item) => item.key === unit);
  const state = operatingState(data, bay);
  return (
    <UnitSchematic
      data={data}
      unit={unit}
      display={display}
      onPart={onPart}
      state={{
        ...stateStyle(state),
        ...(state === 'stale' ? { fill: 'url(#mimic-unit-hatch)' } : {})
      }}
    />
  );
}
