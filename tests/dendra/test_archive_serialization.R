args<-commandArgs(FALSE);here<-dirname(normalizePath(sub('^--file=','',args[grepl('^--file=',args)][1])))
source(file.path(here,'../../scripts/dendra/core.R'));source(file.path(here,'../../scripts/dendra/archive.R'))
p<-list(rows=list(list(date='2000-02-28',count=2,mean=NULL,ok=TRUE,flags=list()),list(date='2000-02-29',count=0L,mean=0,ok=FALSE,flags=list('empty'))))
f<-tempfile()
p$rows[[2]]<-p$rows[[2]][rev(names(p$rows[[2]]))]
archive_write_product(p,f);decoded<-json_read(f)
for(j in seq_along(p$rows))for(k in names(p$rows[[j]]))stopifnot(isTRUE(all.equal(p$rows[[j]][[k]],decoded$rows[[j]][[k]],check.attributes=FALSE)))
unlink(f)
for(k in c('count','date','ok')) {
 bad<-p;bad$rows[[2]][[k]]<-if(k=='count')TRUE else if(k=='date')2 else 1
 result<-try(archive_write_product(bad,f),silent=TRUE);stopifnot(inherits(result,'try-error'),!file.exists(f))
}
cat('Archive serializer preserves explicit nulls, zeros, scalar types and flag arrays; three mixed-type mutations held\n')
