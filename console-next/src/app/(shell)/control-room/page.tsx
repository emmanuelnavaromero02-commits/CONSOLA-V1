import { ControlRoomExperiencePage } from "@/components/control-room/experience/ControlRoomExperiencePage";
import { SapB1ControlRoomEntry } from "@/components/sap-b1/SapB1ControlRoomEntry";

export default function ControlRoomPage() {
  return <ControlRoomExperiencePage entries={<SapB1ControlRoomEntry />} />;
}
