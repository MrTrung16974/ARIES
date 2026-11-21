import { HttpClient } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { APP_CONFIG } from '../config/app-config';
import {
  DataClassification,
  KnowledgeBaseInfo,
  KnowledgeDocument,
  KnowledgeSearchResult,
} from './models';

/** Kho tri thức (ADR-0019). */
@Injectable({ providedIn: 'root' })
export class KnowledgeApi {
  private readonly http = inject(HttpClient);
  private readonly base = `${inject(APP_CONFIG).apiBaseUrl}/v1/knowledge`;

  list(): Observable<KnowledgeBaseInfo[]> {
    return this.http.get<KnowledgeBaseInfo[]>(`${this.base}/bases`);
  }

  create(body: {
    name: string;
    description: string;
    scope: 'personal' | 'workspace';
    data_classification: DataClassification;
  }): Observable<KnowledgeBaseInfo> {
    return this.http.post<KnowledgeBaseInfo>(`${this.base}/bases`, body);
  }

  remove(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/bases/${encodeURIComponent(id)}`);
  }

  documents(id: string): Observable<KnowledgeDocument[]> {
    return this.http.get<KnowledgeDocument[]>(
      `${this.base}/bases/${encodeURIComponent(id)}/documents`,
    );
  }

  upload(id: string, file: File): Observable<KnowledgeDocument> {
    const form = new FormData();
    form.append('file', file, file.name);
    return this.http.post<KnowledgeDocument>(
      `${this.base}/bases/${encodeURIComponent(id)}/documents`,
      form,
    );
  }

  removeDocument(docId: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/documents/${encodeURIComponent(docId)}`);
  }

  reingest(docId: string): Observable<KnowledgeDocument> {
    return this.http.post<KnowledgeDocument>(
      `${this.base}/documents/${encodeURIComponent(docId)}/reingest`,
      {},
    );
  }

  search(id: string, query: string): Observable<KnowledgeSearchResult> {
    return this.http.post<KnowledgeSearchResult>(
      `${this.base}/bases/${encodeURIComponent(id)}/search`,
      { query, top_k: 5 },
    );
  }
}
