export type LearningMode = "answer" | "outline" | "quiz";
export interface Document {
  id: number;
  title: string;
  author: string;
  source: string;
  license: string;
  source_url: string;
  file_name: string;
  num_pages: number;
  num_chunks: number;
  status: "pending" | "processing" | "ready" | "failed";
  error: string;
  indexed_backend: string;
}
export interface Source {
  index: number;
  chunk_id: number;
  document_id: number;
  title: string;
  page_start: number;
  page_end: number;
  content: string;
  score: number;
  source: string;
  license: string;
  file_hash: string;
  section: string;
}
export interface Message {
  id: number;
  role: "user" | "assistant";
  content: string;
  sources: Source[];
  status: string;
  feedback?: "up" | "down" | null;
  created_at?: string;
  metrics?: Metrics;
  phase?: "retrieving" | "generating" | "refusing";
}
export interface Metrics {
  mode?: string;
  learning_mode?: LearningMode;
  retrieval_ms?: number;
  retrieved_sources?: number;
  validated_blocks?: number;
  model?: string;
  first_token_ms?: number | null;
  duration_ms?: number;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
  };
  estimated_cost_cny_peak?: number;
  price_snapshot_date?: string;
  price_source?: string;
  cost_note?: string;
}
export interface Conversation {
  id: number;
  title: string;
  updated_at: string;
}
export interface Task {
  status: string;
  processed: number;
  total: number;
  current: string;
  error: string;
  task_id: string;
}
export interface Health {
  llm_model: string;
  llm_configured: boolean;
  embedding_backend: string;
  embedding_model: string;
  answer_mode: string;
  num_chunks: number;
  num_ready: number;
  version: string;
  num_documents: number;
  library_revision?: string;
}
export interface Evaluation {
  id: number;
  question: string;
  category: string;
  expected_refusal: boolean;
  answer?: string;
  sources?: Source[];
  error?: string;
  refused?: boolean;
  first_token_ms?: number;
  duration_ms?: number;
  judgment?: "correct" | "wrong";
  metrics?: Metrics;
  available?: boolean;
  expected_pages?: number[];
  expected_file_hash?: string;
  book_title?: string;
  retrieval_hit?: boolean | null;
}
export interface EvaluationDataset {
  id: string;
  title: string;
  description: string;
  count: number;
}
