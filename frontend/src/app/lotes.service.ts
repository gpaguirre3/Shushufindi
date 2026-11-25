import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

export type EstadoLote = 'en_cola' | 'procesando' | 'listo' | 'cancelado' | 'error';
export type EstadoCedula = 'ok' | 'sin_datos' | 'error';

export interface ResultadoCedula {
  cedula: string;
  estado: EstadoCedula;
  valor: string;
  total: string;
  detalle: string;
}

export interface Lote {
  id: string;
  archivo: string;
  creado: string;
  estado: EstadoLote;
  total: number;
  actual: number;
  conteo: Record<EstadoCedula, number>;
  error: string;
  zip: boolean;
  log?: string[];
  resultados?: ResultadoCedula[];
  pendientes?: string[];
}

@Injectable({ providedIn: 'root' })
export class LotesService {
  private readonly http = inject(HttpClient);
  private readonly api = '/api';

  crear(archivo: File): Observable<Lote> {
    const datos = new FormData();
    datos.append('archivo', archivo);
    return this.http.post<Lote>(`${this.api}/lotes`, datos);
  }

  listar(): Observable<Lote[]> {
    return this.http.get<Lote[]>(`${this.api}/lotes`);
  }

  ver(id: string): Observable<Lote> {
    return this.http.get<Lote>(`${this.api}/lotes/${id}`);
  }

  cancelar(id: string): Observable<Lote> {
    return this.http.post<Lote>(`${this.api}/lotes/${id}/cancelar`, {});
  }

  urlZip(id: string): string {
    return `${this.api}/lotes/${id}/zip`;
  }

  urlPlantilla(): string {
    return `${this.api}/plantilla`;
  }
}
