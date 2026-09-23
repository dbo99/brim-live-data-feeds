args<-commandArgs(FALSE);here<-dirname(normalizePath(sub('^--file=','',args[grepl('^--file=',args)][1])))
root<-normalizePath(file.path(here,'../..'));p<-file.path(root,'templates/build-dendra-daily.template.yml')
`%||%`<-function(x,y)if(is.null(x))y else x
x<-yaml::read_yaml(p);on<-x[['on']] %||% x[['TRUE']]
stopifnot(identical(names(on),'workflow_dispatch'),identical(x$permissions$contents,'read'),length(x$jobs)==1)
steps<-x$jobs[[1]]$steps;count<-0L
for(step in steps) if(!is.null(step$run)) {
 f<-tempfile(fileext='.sh');writeLines(step$run,f);status<-system2('bash',c('-n',shQuote(f)));unlink(f);stopifnot(status==0);count<-count+1L
 stopifnot(!grepl('main_publisher.py publish|git (commit|push)|contents: write',step$run))
}
text<-readLines(p);stopifnot(!any(grepl('schedule:|cron:',text)),!file.exists(file.path(root,'.github/workflows/build-dendra-daily.yml')))
cat('PASS YAML structure, manual-only trigger, read-only permissions,',count,'embedded bash blocks, inactive path\n')
