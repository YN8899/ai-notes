export type Correction = {revision:number;raw_text:string|null;corrected_text:string|null;reviewed:boolean}
export type OcrDraft = {revision:number;text:string;reviewed:boolean;dirty:boolean}

export function draftFrom(document:Correction):OcrDraft {
  return {revision:document.revision,text:document.corrected_text??document.raw_text??'',reviewed:document.reviewed,dirty:false}
}

// A poll or another tab's save must never advance the base revision of local edits.
export function reconcileDraft(draft:OcrDraft,document:Correction):OcrDraft {
  return draft.dirty?draft:draftFrom(document)
}

export function isOcrActive(status:string):boolean {
  return status==='queued'||status==='running'
}
