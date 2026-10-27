/// <reference types="vite/client" />

interface Window {
  desktopMate?: {
    getAgentServiceUrl: () => Promise<string>;
    getAgentAuthToken: () => Promise<string>;
    getPetFloatEnabled: () => Promise<boolean>;
    setPetFloatEnabled: (enabled: boolean) => Promise<boolean>;
    restoreMainWindow: () => Promise<void>;
    setPetWindowMode: (mode: "compact" | "conversation") => Promise<void>;
    movePetWindow: (deltaX: number, deltaY: number) => Promise<void>;
    speakPetReply: (text: string) => Promise<boolean>;
    stopPetSpeech: () => Promise<void>;
    showItemInFolder: (targetPath: string) => Promise<boolean>;
    selectDirectory: () => Promise<{ path: string; name: string } | null>;
  };
  SpeechRecognition?: SpeechRecognitionConstructor;
  webkitSpeechRecognition?: SpeechRecognitionConstructor;
}

interface SpeechRecognitionConstructor {
  new (): SpeechRecognition;
}

interface SpeechRecognition extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onend: (() => void) | null;
  onerror: (() => void) | null;
  onresult: ((event: SpeechRecognitionEvent) => void) | null;
  start: () => void;
}

interface SpeechRecognitionEvent extends Event {
  results: SpeechRecognitionResultList;
}
