import type { ProjectDetail } from './types'

export default function FilesReport({ detail }: { detail: ProjectDetail }) {
  const items = detail.job?.items ?? []
  return (
    <section className="report-card">
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>File</th>
              <th>Drawing type</th>
              <th>Page</th>
              <th>Status</th>
              <th>Modules</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            {detail.project.files.flatMap((file) => {
              const fileItems = items.filter((item) => item.file_index === file.index)
              return (fileItems.length ? fileItems : [null]).map((item, index) => (
                <tr key={`${file.index}-${index}`}>
                  <td>{file.name}</td>
                  <td>{file.drawing_type}</td>
                  <td>{item ? item.page_index + 1 : '—'}</td>
                  <td>{item?.status ?? 'pending'}</td>
                  <td>{item?.module_ids.join(', ') || '—'}</td>
                  <td>{item?.error || (item?.status === 'skipped' ? 'Skipped by file type' : '—')}</td>
                </tr>
              ))
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}
