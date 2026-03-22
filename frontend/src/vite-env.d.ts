/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
  readonly VITE_REQUEST_TIMEOUT?: string;
  readonly VITE_HEALTH_CHECK_INTERVAL?: string;
  readonly VITE_MAX_UPLOAD_SIZE?: string;
  readonly VITE_APP_NAME?: string;
  readonly VITE_APP_VERSION?: string;
  readonly VITE_AI_MODEL_LABEL?: string;
  readonly VITE_DOCS_URL?: string;
  readonly VITE_WELCOME_MESSAGE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
