// Candidate pipeline stage labels and compact navigation, including planned stages.
import type { CandidateSection } from "../api/candidates";

export const candidateStages: { id: CandidateSection; label: string; active: boolean }[] = [
  { id: "uploaded", label: "Uploaded CV", active: true },
  { id: "integrity", label: "Integrity Check", active: true },
  { id: "parsed", label: "Parsed CV", active: true },
  { id: "ranking", label: "Feature 1 Ranking", active: true },
  { id: "scoring", label: "Feature 2 Scoring", active: true },
  { id: "interview", label: "Selected for Interview", active: true },
  { id: "assessment", label: "Feature 6 Assessment", active: false },
  { id: "f5", label: "F5 QA Analysis", active: true },
  { id: "review", label: "Review", active: true },
  { id: "rejected", label: "Rejected", active: true },
];

export const activeCandidateStages = new Set<CandidateSection>(
  candidateStages.filter((item) => item.active).map((item) => item.id),
);

export default function CandidateStageNav({ section, onSelect }: {
  section: CandidateSection;
  onSelect: (section: CandidateSection) => void;
}) {
  return <nav className="candidate-sections" aria-label="Candidate pipeline sections">
    {candidateStages.map((item) => <button key={item.id} type="button"
      className={`candidate-section${item.id === section ? " active" : ""}${item.active ? "" : " upcoming"}`}
      disabled={!item.active} onClick={() => onSelect(item.id)}
      aria-current={item.id === section ? "page" : undefined}
      title={item.active ? undefined : "This pipeline step will be available in a future update."}>
      <span>{item.label}</span>{!item.active && <small>Coming soon</small>}
    </button>)}
  </nav>;
}
