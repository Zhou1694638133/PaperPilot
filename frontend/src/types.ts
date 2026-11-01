export type Mode = 'live' | 'demo'
export type View = 'discover' | 'local' | 'library' | 'detail' | 'skills' | 'jobs' | 'preferences' | 'compare' | 'glossary' | 'subscriptions' | 'weekly' | 'feedback'
export interface Profile {
  interests: string[]; include_keywords: string[]; exclude_keywords: string[]
  days: number; page_size: number; language: string; topic_weight?: number
}
export interface Collection { saved: boolean; read: boolean; interested: boolean; tags: string[]; notes: string }
export interface Evidence { text: string; location: string }
export interface Recommendation {
  score: number; interests: string[]; evidence: Evidence[]; coverage: string
  features: {name: string; value: number | null; weight: number}[]
  unknowns: string[]; method: string; run_id: string; reason?: string
}
export interface Feedback { value: 'none' | 'relevant' | 'not_relevant' | 'too_basic' | 'lacks_experiments'; reason: string; adjustment: number; updated_at?: string | null }
export interface Paper {
  feedback?: Feedback
  uploaded_at?: string; abstract_revision?: string; abstract_source?: {location: string; document_hash: string}

  id: string; version: number; title: string; authors: string[]; abstract: string
  published: string; updated: string; categories: string[]; url: string; pdf_url: string
  doi?: string | null; source: string; mode: Mode; collection: Collection; recommendation: Recommendation | null
  publication?: Publication
  semantic?: SemanticState
  terminology?: { job: Job | null; message: string }
}
export interface SemanticResult {
  score: number; model: string; method: string; coverage: string
  best_interest_score: number; mean_interest_score: number
  interests: {interest: string; score: number; reason: string; evidence: Evidence[]}[]
  unknowns: string[]; markdown: string
}
export interface SemanticState {
  status: string; message: string; job: Job | null; result: SemanticResult | null
}
export interface Publication { status: string; ccf_rank: string | null; venue: string | null; year?: number; evidence_url: string | null; evidence_text?: string; catalog_url?: string; catalog_edition?: string; note: string }
export interface ConferenceDiscovery {
  verified_count?: number; priority_status?: string
  scope: string; base_limit: number; recall_candidates: number; added: number; indexed_papers: number; note: string
  sources: {source: string; url?: string; status: string; count?: number; message?: string}[]
  queries: string[]; links: {name: string; url: string; automatic: boolean}[]
}
export interface SearchResult {
  discovery?: ConferenceDiscovery; ranking_scope?: string

  papers: Paper[]; total: number; start: number; limit: number; query: string
  mode: Mode; cached: boolean; fetched_at: string; run_id: string; warning?: string; has_more?: boolean; next_start?: number
}
export interface Health { status: string; mode: Mode; model_configured: boolean; model: string; worker?: { online: boolean; last_seen: number | null; concurrency: number } }
export interface Skill {
  id: string; name: string; description: string; version: string; available: boolean; requires_model: boolean
  input_schema: Record<string, unknown>; output_schema: Record<string, unknown>
}
export interface Job {
  can_retry?: boolean
  translation_state?: TranslationState | null
  abstract_revision?: string | null

  translation_job_id?: string | null
  id: string; skill_id: string; paper_id: string; mode?: Mode; status: string; stage: string; progress: number
  created_at: string; updated_at: string; error?: string | null; attempts: number; result?: unknown; requires_confirmation?: boolean
  scope?: 'abstract' | 'fulltext'; paper_version?: number
}
export interface TextBlock { failure?: TranslationIssue | null; classification?: string;  id: string; text: string; kind: string; section: string; location: string; translatable: boolean; translation?: string | null; status?: string; display_text?: string; display_translation?: string; display_section?: string; supplemental?: boolean; source_url?: string }
export interface PaperDocument { paper_id: string; version: number; parser_version: string; source_url: string; document_hash: string; method: string; status: string; warnings: string[]; blocks: TextBlock[]; paragraph_count: number; note: string; base_paragraph_count?: number; source_enrichment?: { notes: string[]; pdf_url: string; references: TextBlock[] } }
export interface ReadingSection { text: string; evidence: string[] }
export interface Reading { coverage: string; markdown: string; research_question: ReadingSection; method: ReadingSection; experiments: ReadingSection; limitations: ReadingSection; research_relation: ReadingSection; unknowns: string[]; evidence_locations?: {field: string; quote: string; paragraph_id: string; section: string; location: string}[] }
export interface Alignment { job: Job; blocks: TextBlock[]; completed: number; total: number; glossary_version: number; document_hash: string | null; checkpoint_completed?: number; checkpoint_total?: number; supplemental_count?: number }




export interface TranslationIssue { paragraph_id: string; kind: string; reason: string; location: string; source_hash: string; error?: string; phase?: string }
export interface TranslationPreflight { version: string; total: number; counts: Record<string, number>; items: TranslationIssue[]; note: string }
export interface TranslationState { preflight: TranslationPreflight; failures: TranslationIssue[] }
