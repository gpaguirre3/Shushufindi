import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { Subscription, switchMap, takeWhile, timer } from 'rxjs';

import { EstadoCedula, EstadoLote, Lote, LotesService } from './lotes.service';

const ACTIVOS: EstadoLote[] = ['en_cola', 'procesando'];
const EXTENSIONES = ['.xlsx', '.xlsm', '.csv'];

@Component({
  selector: 'app-root',
  imports: [DatePipe],
  templateUrl: './app.html',
})
export class App {
  private readonly servicio = inject(LotesService);
  private seguimiento?: Subscription;

  protected readonly archivo = signal<File | null>(null);
  protected readonly arrastrando = signal(false);
  protected readonly enviando = signal(false);
  protected readonly error = signal('');
  protected readonly lote = signal<Lote | null>(null);
  protected readonly historial = signal<Lote[]>([]);

  protected readonly activo = computed(() => {
    const l = this.lote();
    return !!l && ACTIVOS.includes(l.estado);
  });
  protected readonly porcentaje = computed(() => {
    const l = this.lote();
    return l && l.total ? Math.round((l.actual / l.total) * 100) : 0;
  });
  protected readonly urlPlantilla = this.servicio.urlPlantilla();

  protected readonly etiquetaLote: Record<EstadoLote, string> = {
    en_cola: 'En cola',
    procesando: 'Procesando',
    listo: 'Terminado',
    cancelado: 'Cancelado',
    error: 'Error',
  };
  protected readonly etiquetaCedula: Record<EstadoCedula, string> = {
    ok: 'Consultada',
    sin_datos: 'Sin registros',
    error: 'Error',
  };

  constructor() {
    inject(DestroyRef).onDestroy(() => this.seguimiento?.unsubscribe());
    this.cargarHistorial();
  }

  protected elegir(evento: Event): void {
    const input = evento.target as HTMLInputElement;
    this.asignar(input.files?.[0] ?? null);
    input.value = '';
  }

  protected soltar(evento: DragEvent): void {
    evento.preventDefault();
    this.arrastrando.set(false);
    this.asignar(evento.dataTransfer?.files?.[0] ?? null);
  }

  protected sobre(evento: DragEvent, dentro: boolean): void {
    evento.preventDefault();
    this.arrastrando.set(dentro);
  }

  private asignar(archivo: File | null): void {
    this.error.set('');
    if (!archivo) return;
    const nombre = archivo.name.toLowerCase();
    if (!EXTENSIONES.some((e) => nombre.endsWith(e))) {
      this.error.set('Formato no admitido. Usa un archivo .xlsx o .csv');
      return;
    }
    this.archivo.set(archivo);
  }

  protected quitarArchivo(): void {
    this.archivo.set(null);
  }

  protected iniciar(): void {
    const archivo = this.archivo();
    if (!archivo || this.enviando()) return;
    this.enviando.set(true);
    this.error.set('');
    this.servicio.crear(archivo).subscribe({
      next: (lote) => {
        this.enviando.set(false);
        this.archivo.set(null);
        this.lote.set(lote);
        this.seguir(lote.id);
        this.cargarHistorial();
      },
      error: (e: HttpErrorResponse) => {
        this.enviando.set(false);
        this.error.set(e.error?.error ?? 'No se pudo iniciar la consulta');
      },
    });
  }

  protected abrir(id: string): void {
    this.seguir(id);
  }

  protected cancelar(): void {
    const l = this.lote();
    if (!l || !confirm('¿Detener la consulta? Las cédulas ya consultadas quedan en el ZIP.')) return;
    this.servicio.cancelar(l.id).subscribe();
  }

  protected urlZip(id: string): string {
    return this.servicio.urlZip(id);
  }

  private seguir(id: string): void {
    this.seguimiento?.unsubscribe();
    this.seguimiento = timer(0, 3000)
      .pipe(
        switchMap(() => this.servicio.ver(id)),
        takeWhile((l) => ACTIVOS.includes(l.estado), true),
      )
      .subscribe({
        next: (l) => {
          this.lote.set(l);
          if (!ACTIVOS.includes(l.estado)) this.cargarHistorial();
        },
        error: () => this.error.set('Se perdió la conexión con el servidor'),
      });
  }

  private cargarHistorial(): void {
    this.servicio.listar().subscribe({
      next: (lotes) => {
        this.historial.set(lotes);
        const enCurso = lotes.find((l) => ACTIVOS.includes(l.estado));
        if (!this.lote() && enCurso) this.seguir(enCurso.id);
      },
      error: () => this.error.set('No se pudo conectar con el servidor (python app.py)'),
    });
  }
}
