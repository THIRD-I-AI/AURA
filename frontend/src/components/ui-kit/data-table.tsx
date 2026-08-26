/**
 * DataTable — generic sortable/filterable dense data table.
 *
 * Real <table> markup (native semantics; a `title`-tooltip truncation trick
 * needs no table-layout:fixed games this way). Client-side sort/filter only —
 * datasets here are tens of rows, not the scale that needs a query-builder or
 * virtualization (.claude/rules/frontend.md: "Virtualize only once a dataset
 * realistically renders 100+ concurrent DOM rows").
 *
 * Loading/empty/error states compose the existing EmptyState/Skeleton
 * primitives rather than reinventing them (frontend.md).
 */
import * as React from 'react'
import { ArrowUpDown, ChevronDown, ChevronUp } from 'lucide-react'

import { cn } from '@/lib/cn'
import { Button } from '@/components/ui-kit/button'
import { EmptyState } from '@/components/ui-kit/empty-state'
import { Skeleton } from '@/components/ui-kit/skeleton'

export interface ColumnDef<T> {
  key: string
  header: string
  accessor: (row: T) => React.ReactNode
  /** Enables sorting on this column; ignored unless `sortValue` is also set. */
  sortable?: boolean
  sortValue?: (row: T) => string | number
  /** Text this column contributes to the filter match; falls back to `sortValue`. */
  filterValue?: (row: T) => string
  align?: 'left' | 'right'
  /** Wrap the rendered cell in a truncating div with a `title` tooltip. */
  truncate?: boolean
  className?: string
}

export interface DataTableProps<T> {
  columns: ColumnDef<T>[]
  /** `null` = loading, matching this codebase's `X | null` loading convention. */
  rows: T[] | null
  getRowKey?: (row: T, index: number) => React.Key
  onRowClick?: (row: T) => void
  filterPlaceholder?: string
  emptyTitle?: string
  emptyDescription?: string
  /** Set when the initial load failed (only consulted while `rows` is null). */
  error?: string | null
  errorTitle?: string
  onRetry?: () => void
  className?: string
}

type SortState = { key: string; dir: 'asc' | 'desc' } | null

export function DataTable<T>({
  columns,
  rows,
  getRowKey,
  onRowClick,
  filterPlaceholder = 'Filter…',
  emptyTitle = 'No results',
  emptyDescription,
  error = null,
  errorTitle = 'Unavailable',
  onRetry,
  className,
}: DataTableProps<T>) {
  const [sort, setSort] = React.useState<SortState>(null)
  const [filter, setFilter] = React.useState('')

  const filterableCols = React.useMemo(
    () => columns.filter((c) => c.filterValue || c.sortValue),
    [columns],
  )

  const visibleRows = React.useMemo(() => {
    if (!rows) return []
    let out = rows
    const needle = filter.trim().toLowerCase()
    if (needle) {
      out = out.filter((row) =>
        filterableCols.some((c) => {
          const get = c.filterValue ?? c.sortValue!
          return String(get(row)).toLowerCase().includes(needle)
        }),
      )
    }
    if (sort) {
      const col = columns.find((c) => c.key === sort.key)
      if (col?.sortValue) {
        const dir = sort.dir === 'asc' ? 1 : -1
        out = [...out].sort((a, b) => {
          const av = col.sortValue!(a)
          const bv = col.sortValue!(b)
          const cmp = typeof av === 'number' && typeof bv === 'number'
            ? av - bv
            : String(av).localeCompare(String(bv))
          return cmp * dir
        })
      }
    }
    return out
  }, [rows, filter, filterableCols, sort, columns])

  const toggleSort = (col: ColumnDef<T>) => {
    if (!col.sortable || !col.sortValue) return
    setSort((prev) => {
      if (prev?.key !== col.key) return { key: col.key, dir: 'asc' }
      return { key: col.key, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
    })
  }

  const hasData = rows !== null && rows.length > 0

  return (
    <div className={cn('flex flex-col gap-2', className)}>
      {hasData && filterableCols.length > 0 && (
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder={filterPlaceholder}
          aria-label={filterPlaceholder}
          className={cn(
            'rounded-none border border-border-hairline bg-secondary px-3 py-1.5 font-mono text-xs text-card-foreground',
            'placeholder:text-text-tertiary outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50',
          )}
        />
      )}

      {rows === null && !error && (
        <div className="border border-border-hairline" role="status" aria-label="Loading">
          {[0, 1, 2].map((r) => (
            <div
              key={r}
              className={cn('flex items-center gap-4 px-3 py-2', r > 0 && 'border-t border-border-hairline')}
            >
              {columns.map((c, i) => (
                <Skeleton key={c.key} className={cn('h-3', i === 0 ? 'w-1/4' : 'flex-1')} />
              ))}
            </div>
          ))}
        </div>
      )}

      {rows === null && error && (
        <EmptyState
          intent="error"
          title={errorTitle}
          description={error}
          action={onRetry && <Button variant="outline" size="sm" onClick={onRetry}>Retry</Button>}
        />
      )}

      {rows !== null && rows.length === 0 && (
        <EmptyState intent="empty" title={emptyTitle} description={emptyDescription} />
      )}

      {hasData && (
        <div className="overflow-x-auto border border-border-hairline">
          <table className="w-full border-collapse font-mono text-xs">
            <thead>
              <tr className="border-b border-border-hairline">
                {columns.map((col) => (
                  <th
                    key={col.key}
                    scope="col"
                    aria-sort={
                      sort?.key === col.key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined
                    }
                    className={cn(
                      'px-3 py-2 text-left align-middle',
                      col.align === 'right' && 'text-right',
                      col.className,
                    )}
                  >
                    {col.sortable && col.sortValue ? (
                      <button
                        type="button"
                        onClick={() => toggleSort(col)}
                        className={cn(
                          'inline-flex items-center gap-1 font-mono text-2xs font-semibold uppercase tracking-wider text-text-secondary',
                          'outline-none hover:text-text-primary focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50',
                          col.align === 'right' && 'flex-row-reverse',
                        )}
                      >
                        {col.header}
                        {sort?.key === col.key ? (
                          sort.dir === 'asc' ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />
                        ) : (
                          <ArrowUpDown className="size-3 text-text-tertiary" />
                        )}
                      </button>
                    ) : (
                      <span className="font-mono text-2xs font-semibold uppercase tracking-wider text-text-secondary">
                        {col.header}
                      </span>
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border-hairline">
              {visibleRows.length === 0 ? (
                <tr>
                  <td colSpan={columns.length}>
                    <EmptyState intent="empty" title="No matches" description="No rows match your filter." />
                  </td>
                </tr>
              ) : (
                visibleRows.map((row, i) => (
                  <tr
                    key={getRowKey ? getRowKey(row, i) : i}
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                    // Stay `role="row"` (the <tr> default) so table nav semantics and
                    // getAllByRole('row') queries survive — a nested activation target
                    // shouldn't reclassify the row itself as a generic button.
                    tabIndex={onRowClick ? 0 : undefined}
                    onKeyDown={
                      onRowClick
                        ? (e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault()
                              onRowClick(row)
                            }
                          }
                        : undefined
                    }
                    className={cn(
                      onRowClick && 'cursor-pointer outline-none hover:bg-secondary/60 focus-visible:bg-secondary/60',
                    )}
                  >
                    {columns.map((col) => {
                      const content = col.accessor(row)
                      const cell = col.truncate ? (
                        <div className="truncate" title={typeof content === 'string' ? content : undefined}>
                          {content}
                        </div>
                      ) : (
                        content
                      )
                      return (
                        <td
                          key={col.key}
                          className={cn(
                            'px-3 py-2 align-middle text-text-primary',
                            col.truncate && 'max-w-0',
                            col.align === 'right' && 'text-right',
                            col.className,
                          )}
                        >
                          {cell}
                        </td>
                      )
                    })}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
