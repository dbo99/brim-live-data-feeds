# Parse pinned builder requirements without sourcing it or executing any provider code.
args <- commandArgs(TRUE)
script <- sub('^--file=', '', commandArgs(FALSE)[grepl('^--file=', commandArgs(FALSE))][1])
root <- normalizePath(file.path(dirname(script), '../..'))
paths <- c('scripts/build_scan_soil_moisture_latest.R',
           '.github/workflows/build-scan-soil-moisture-latest.yml',
           '.github/actions/setup-r-hardened/action.yml',
           'templates/soil-moisture/scan.template.yml')
expressions <- parse(file.path(root, paths[1]))
assignments <- Filter(function(e) is.call(e) && identical(e[[1]], as.name('<-')) &&
                        identical(e[[2]], as.name('required_pkgs')), as.list(expressions))
stopifnot(length(assignments)==1L)
literal <- assignments[[1]][[3]]
stopifnot(is.call(literal), identical(literal[[1]], as.name('c')),
          all(vapply(as.list(literal)[-1], is.character, logical(1))))
required <- unlist(as.list(literal)[-1], use.names=FALSE)
direct <- character()
walk <- function(e) {
  if (!is.call(e)) return(invisible(NULL))
  if (identical(e[[1]], as.name('::')) || identical(e[[1]], as.name(':::')))
    direct <<- c(direct, as.character(e[[2]]))
  if (identical(e[[1]], as.name('library')) || identical(e[[1]], as.name('require')))
    direct <<- c(direct, as.character(e[[2]]))
  for (part in as.list(e)[-1]) if (!missing(part)) walk(part)
}
for (e in expressions) walk(e)
stopifnot(all(unique(direct) %in% c(required, 'base', 'utils', 'stats')))
setup_packages <- function(path) {
  lines <- readLines(file.path(root,path),warn=FALSE)
  sub('^.*any::([A-Za-z0-9.]+).*$', '\\1', lines[grepl('any::',lines)])
}
active <- setup_packages(paths[2]); inactive <- setup_packages(paths[4])
stopifnot(all(required %in% active),all(required %in% inactive))
available <- vapply(required, requireNamespace, logical(1), quietly=TRUE)
report <- list(status=if(all(available)) 'dependencies_available' else 'missing_local_dependencies',
               required=required,direct=sort(unique(direct)),active_setup=active,inactive_setup=inactive,
               available=as.list(available),missing=required[!available],
               source_sha256=setNames(lapply(paths,function(p) digest::digest(file=file.path(root,p),algo='sha256')),paths),
               builder_executed=FALSE,provider_requests=0L,
               note='Source/setup completeness checked locally; hosted setup/package installation is unexecuted.')
output <- args[match('--output',args)+1L]
stopifnot(length(output)==1L,!is.na(output));dir.create(dirname(output),recursive=TRUE,showWarnings=FALSE)
jsonlite::write_json(report,output,auto_unbox=TRUE,pretty=TRUE,null='null')
cat(report$status,'\n')
if (!all(available) && !'--audit-only' %in% args) quit(status=2)
