// Domain types generated from the backend OpenAPI schema (`make web-types` → api-types.ts).
// Do not hand-write API shapes here: change the pydantic models and regenerate.
import type { components } from './api-types'

type S = components['schemas']

export type ActivityEntry = S['ActivityEntry']
export type ActivityIntegrityResponse = S['ActivityIntegrityResponse']
export type BoardPrice = S['BoardPrice']
export type BoardSummaryRow = S['BoardSummaryRow']
export type BoundingBox = S['BoundingBox']
export type Cell = S['Cell']
export type CellDraft = S['CellDraft']
export type Column = S['Column']
export type ColumnDraft = S['ColumnDraft']
export type DimensionChain = S['DimensionChain']
export type DimensionMark = S['DimensionMark']
export type EdgeBanding = S['EdgeBanding']
export type EdgeSummaryRow = S['EdgeSummaryRow']
export type FurnitureModule = S['FurnitureModule']
export type HardwareItem = S['HardwareItem']
export type HardwareSummaryRow = S['HardwareSummaryRow']
export type Issue = S['Issue']
export type Job = S['Job']
export type JobItem = S['JobItem']
export type MaterialSpec = S['MaterialSpec']
export type MaterialSummary = S['MaterialSummary']
export type ModuleDraft = S['ModuleDraft']
export type ModuleModel = S['ModuleModel']
export type ModuleVersion = S['ModuleVersion']
export type ModuleView = S['ModuleView']
export type Override = S['Override']
export type Panel = S['Panel']
export type PanelSolid = S['PanelSolid']
export type Pricing = S['Pricing']
export type ProcessCell = S['ProcessCell']
export type ProcessLine = S['ProcessLine']
export type ProcessMatrixRow = S['ProcessMatrixRow']
export type ProcessSummary = S['ProcessSummary']
export type ProcessTotal = S['ProcessTotal']
export type Project = S['Project']
export type ProjectFile = S['ProjectFile']
export type ProjectPricing = S['ProjectPricing']
export type Question = S['Question']
export type QuoteComparison = S['QuoteComparison']
export type QuoteLine = S['QuoteLine']
export type QuoteMatch = S['QuoteMatch']
export type TextRun = S['TextRun']
export type Valuation = S['Valuation']
export type ValuationLine = S['ValuationLine']
export type ViewCheck = S['ViewCheck']
export type AuthState = S['AuthMeResponse']
export type ProjectDetail = S['ProjectDetailResponse']
export type ProjectModule = S['ModuleListEntry']
export type ProjectSummary = S['ProjectListResponse']
export type ProjectReviewItem = S['ReviewEntry']
export type ValuationGroupAmount = S['GroupAmount']

export type Confidence = S['ConfidenceLevel']
export type MaterialKind = MaterialSpec['kind']
export type FrontType = Cell['front']
export type IssueSeverity = Issue['severity']
export type Provenance = Cell['height_source']
export type QuestionReason = Question['reason']
export type HardwareCategory = HardwareItem['category']
export type ProcessKind = ProcessLine['process']
export type ProcessUnit = ProcessLine['unit']
export type CabinetKind = FurnitureModule['kind']
export type ValuationGroup = ValuationLine['group']

export const materialColors: Record<MaterialKind, string> = {
  laminated_board: '#d8c7a8',
  veneer: '#b07d4b',
  lacquered_mdf: '#ececec',
  hpl: '#3a3a3a',
  mirror: '#9fb7c9',
  glass: '#c8e0ea',
  stone: '#8f8f8f',
  upholstery: '#7a6b5d',
  metal: '#777777',
  other: '#cfcfcf',
}

// Every MaterialKind, in materialColors order.
export const materialKinds = Object.keys(materialColors) as MaterialKind[]
