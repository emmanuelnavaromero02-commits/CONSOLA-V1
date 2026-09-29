import { Suspense } from "react";

import { ControlRoomExperiencePage } from "@/components/control-room/experience/ControlRoomExperiencePage";
import { ControlRoomPhases } from "@/components/control-room/phases/ControlRoomPhases";
import { SapB1ControlRoomEntry } from "@/components/sap-b1/SapB1ControlRoomEntry";

export default function ControlRoomPage() {
  return (
    <Suspense fallback={null}>
      <ControlRoomPhases
        entiende={<ControlRoomExperiencePage entries={<SapB1ControlRoomEntry />} />}
      />
    </Suspense>
  );
}
