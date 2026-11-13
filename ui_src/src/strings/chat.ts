// Chat stage vocabulary (Phase-2 Wave 7): the chat page's words, every
// @ant-design/x default we render overridden from this table ([D12]).
import tableJson from "./chat.json";
import { strings as appTable, type AppStrings } from "./app";

export interface ChatStrings {
  chatActivitySearching: string;
  chatAriaLog: string;
  chatBlockedBody: string;
  chatBody: string;
  chatCitationsTitle: string;
  chatDisabledBody: string;
  chatErrorTurn: string;
  chatHistoryEmpty: string;
  chatHistoryTitle: string;
  chatPlaceholder: string;
  chatReceiptCost: string;
  chatReceiptModel: string;
  chatReceiptTime: string;
  chatReceiptTurns: string;
  chatResumeLabel: string;
  chatSendHint: string;
  chatSessionActive: string;
  chatSessionNew: string;
  chatStatusChecks: string;
  chatStatusEnabled: string;
  chatStatusTitle: string;
  chatSpeechRecording: string;
  chatStopLabel: string;
  chatStopped: string;
  chatTitle: string;
  chatWarningLabel: string;
}

export const strings: AppStrings & ChatStrings = {
  ...appTable,
  ...tableJson,
};
