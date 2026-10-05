// Bounded Arrow/Parquet I/O for bulk_csv_import.py. No network or science rules.
// Build with C++17 and the existing pkg-config arrow/parquet libraries.
#include <arrow/api.h>
#include <arrow/csv/api.h>
#include <arrow/io/api.h>
#include <parquet/arrow/reader.h>
#include <parquet/arrow/writer.h>
#include <cmath>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>

using A = std::shared_ptr<arrow::Array>;
using S = std::shared_ptr<arrow::Schema>;
void check(const arrow::Status& s) { if (!s.ok()) throw std::runtime_error(s.ToString()); }
template<class T> T take(arrow::Result<T> x) {
  if (!x.ok()) throw std::runtime_error(x.status().ToString());
  return std::move(x).ValueOrDie();
}
template<class T> A finish(T& b) { return take(b.Finish()); }
A constant(const std::string& v, int64_t n) {
  return take(arrow::MakeArrayFromScalar(arrow::StringScalar(v), n));
}
std::vector<std::vector<std::string>> config(const std::string& path) {
  auto input=take(arrow::io::ReadableFile::Open(path));
  auto ro=arrow::csv::ReadOptions::Defaults(); ro.autogenerate_column_names=true;
  auto co=arrow::csv::ConvertOptions::Defaults(); co.auto_dict_encode=false;
  // These small control CSVs always contain strings, including empty strings.
  for(int i=0;i<128;i++) co.column_types["f"+std::to_string(i)]=arrow::utf8();
  auto reader=take(arrow::csv::TableReader::Make(arrow::io::default_io_context(),input,ro,
       arrow::csv::ParseOptions::Defaults(),co));
  auto table=take(take(reader->Read())->CombineChunks());
  std::vector<std::vector<std::string>> out;
  for(int64_t j=0;j<table->num_rows();j++) {
    std::vector<std::string> row;
    for(int i=0;i<table->num_columns();i++)
      row.push_back(std::static_pointer_cast<arrow::StringArray>(table->column(i)->chunk(0))->GetString(j));
    out.push_back(row);
  }
  return out;
}
std::unique_ptr<parquet::arrow::FileWriter> writer(const std::string& p, S s) {
  if(std::filesystem::exists(p)) throw std::runtime_error("Refuse existing output: "+p);
  auto out=take(arrow::io::FileOutputStream::Open(p));
  auto props=parquet::WriterProperties::Builder().compression(parquet::Compression::ZSTD)
      ->max_row_group_length(65536)->build();
  auto ap=parquet::ArrowWriterProperties::Builder().store_schema()->build();
  return take(parquet::arrow::FileWriter::Open(*s,arrow::default_memory_pool(),out,props,ap));
}
int64_t stamp(const std::string& s) {
  if(s.size()!=19) throw std::runtime_error("Expected naive second-resolution timestamp");
  std::tm t{}; std::istringstream in(s); in>>std::get_time(&t,"%Y-%m-%d %H:%M:%S");
  if(in.fail()) throw std::runtime_error("Invalid source timestamp");
  auto seconds=timegm(&t); char out[32]; strftime(out,sizeof(out),"%Y-%m-%d %H:%M:%S",&t);
  if(s!=out) throw std::runtime_error("Noncanonical source timestamp");
  // Epoch used ONLY to encode Arrow's timezone-free timestamp, not a UTC claim.
  return seconds;
}
struct Target {
  std::vector<std::string> c;
  int ordinal; double multiplier; std::unique_ptr<parquet::arrow::FileWriter> w;
  int64_t n=0,zeros=0,negative=0,oor=0,duplicates=0,sentinels=0,jumps=0,last=0,first=0;
  double lo=std::numeric_limits<double>::infinity(),hi=-std::numeric_limits<double>::infinity();
  double previous=0,max_jump=0;
};
S native_schema() {
  return arrow::schema({arrow::field("export_local_series_key",arrow::utf8()),
    arrow::field("source_timestamp_naive",arrow::timestamp(arrow::TimeUnit::MICRO)),
    arrow::field("exported_value",arrow::float64()),arrow::field("source_csv_record_1_based",arrow::int64()),
    arrow::field("source_csv_column_1_based",arrow::int32()),arrow::field("exported_unit",arrow::utf8()),
    arrow::field("proposed_stream_id",arrow::utf8()),arrow::field("accepted_stream_id",arrow::utf8()),
    arrow::field("canonical_vwc_percent",arrow::float64()),arrow::field("timestamp_semantics_status",arrow::utf8()),
    arrow::field("station_id",arrow::utf8()),arrow::field("depth_cm",arrow::float64()),
    arrow::field("source_file_sha256",arrow::utf8())});
}
void native(const std::string& input,const std::string& control,const std::string& output,int block) {
  auto cs=config(control); std::vector<Target> targets;
  auto ro=arrow::csv::ReadOptions::Defaults(); ro.autogenerate_column_names=true;
  ro.skip_rows=1; ro.block_size=block; ro.use_threads=false;
  auto co=arrow::csv::ConvertOptions::Defaults(); co.column_types["f0"]=arrow::utf8();
  co.include_columns={"f0"}; co.null_values={""}; co.strings_can_be_null=false;
  for(auto c:cs) {
    if(c.size()!=10) throw std::runtime_error("Invalid target control");
    Target t; t.c=c; t.ordinal=std::stoi(c[0]); t.multiplier=std::stod(c[4]);
    if(t.ordinal<2 || (t.multiplier!=1 && t.multiplier!=100)) throw std::runtime_error("Invalid target");
    std::string name="f"+std::to_string(t.ordinal-1);
    co.include_columns.push_back(name);co.column_types[name]=arrow::utf8();targets.push_back(std::move(t));
  }
  auto reader=take(arrow::csv::StreamingReader::Make(arrow::io::default_io_context(),
      take(arrow::io::ReadableFile::Open(input)),ro,arrow::csv::ParseOptions::Defaults(),co));
  int64_t row=0,batches=0,max_rows=0; auto schema=native_schema();
  while(auto batch=take(reader->Next())) {
    batches++;max_rows=std::max(max_rows,batch->num_rows());
    auto times=std::static_pointer_cast<arrow::StringArray>(batch->column(0));
    std::vector<int64_t> ts;ts.reserve(batch->num_rows());
    for(int64_t j=0;j<batch->num_rows();j++)ts.push_back(stamp(times->GetString(j)));
    for(size_t k=0;k<targets.size();k++) {
      auto& t=targets[k];auto v=std::static_pointer_cast<arrow::StringArray>(batch->column(k+1));
      arrow::TimestampBuilder tb(arrow::timestamp(arrow::TimeUnit::MICRO),arrow::default_memory_pool());
      arrow::DoubleBuilder vb,cb,db;arrow::Int64Builder rb;arrow::Int32Builder ob;
      int64_t count=0;
      for(int64_t j=0;j<batch->num_rows();j++) {
        std::string token=v->GetString(j);if(token.empty())continue;
        size_t used=0;double value=std::stod(token,&used),canonical=value*t.multiplier;
        if(used!=token.size()||!std::isfinite(value)||!std::isfinite(canonical))throw std::runtime_error("Invalid finite value");
        int64_t time=ts[j];
        if(t.n && time<t.last)throw std::runtime_error("Source timestamp regression");
        if(t.n) {
          t.duplicates+=(time==t.last);double jump=std::abs(canonical-t.previous);
          t.max_jump=std::max(t.max_jump,jump);t.jumps+=(jump>50);
        }else t.first=time;
        t.n++;t.last=time;t.previous=canonical;t.zeros+=(value==0);t.negative+=(value<0);
        t.oor+=(canonical<0||canonical>100);t.lo=std::min(t.lo,value);t.hi=std::max(t.hi,value);
        t.sentinels+=(value==7999||value==-235||value==9999||value==-9999||value==-999||value==999);
        check(tb.Append(time*1000000));check(vb.Append(value));check(cb.Append(canonical));check(rb.Append(row+j+1));
        check(ob.Append(t.ordinal));if(t.c[8].empty())check(db.AppendNull());else check(db.Append(std::stod(t.c[8])));
        count++;
      }
      if(!count)continue;
      if(!t.w)t.w=writer(output+"/"+t.c[1]+".parquet",schema);
      auto table=arrow::Table::Make(schema,{constant(t.c[2],count),finish(tb),finish(vb),finish(rb),finish(ob),
          constant(t.c[3],count),constant(t.c[5],count),constant(t.c[6],count),finish(cb),
          constant("NAIVE_SOURCE_TIME; UTC_UNASSIGNED",count),constant(t.c[7],count),finish(db),constant(t.c[9],count)});
      check(t.w->WriteTable(*table,65536));
    }
    row+=batch->num_rows();
  }
  std::cout<<"asset\trows\tzeros\tnegative\tout_of_range\tmin\tmax\tduplicates\tsentinel_like\tjumps_over_50pp\tmax_jump_pp\tfirst_epoch_naive\tlast_epoch_naive\n"<<std::setprecision(17);
  for(auto& t:targets) {
    if(t.w)check(t.w->Close());
    std::cout<<t.c[1]<<'\t'<<t.n<<'\t'<<t.zeros<<'\t'<<t.negative<<'\t'<<t.oor<<'\t';
    if(t.n)std::cout<<t.lo;std::cout<<'\t';if(t.n)std::cout<<t.hi;
    std::cout<<'\t'<<t.duplicates<<'\t'<<t.sentinels<<'\t'<<t.jumps<<'\t'<<t.max_jump<<'\t'<<t.first<<'\t'<<t.last<<'\n';
  }
  std::cerr<<"{\"source_records\":"<<row<<",\"batches\":"<<batches<<",\"max_batch_rows\":"<<max_rows
           <<",\"arrow_peak_bytes\":"<<arrow::default_memory_pool()->max_memory()<<"}\n";
}
std::shared_ptr<arrow::DataType> dtype(const std::string& s) {
  if(s=="string")return arrow::utf8();if(s=="double")return arrow::float64();
  if(s=="int64")return arrow::int64();if(s=="bool")return arrow::boolean();
  throw std::runtime_error("Unsupported table type");
}
void table(const std::string& input,const std::string& output,const std::string& types) {
  auto ro=arrow::csv::ReadOptions::Defaults();ro.use_threads=false;ro.block_size=1048576;
  auto co=arrow::csv::ConvertOptions::Defaults();co.null_values={""};co.strings_can_be_null=true;
  for(auto c:config(types))co.column_types[c.at(0)]=dtype(c.at(1));
  auto reader=take(arrow::csv::StreamingReader::Make(arrow::io::default_io_context(),
      take(arrow::io::ReadableFile::Open(input)),ro,arrow::csv::ParseOptions::Defaults(),co));
  auto w=writer(output,reader->schema());
  while(auto batch=take(reader->Next()))check(w->WriteTable(*arrow::Table::Make(batch->schema(),batch->columns()),65536));
  check(w->Close());
}
void readback(const std::string& path,bool extract,const std::string& start,const std::string& end,const std::string& subset="") {
  auto reader=take(parquet::arrow::OpenFile(take(arrow::io::ReadableFile::Open(path)),arrow::default_memory_pool()));
  reader->set_batch_size(65536);auto batches=take(reader->GetRecordBatchReader());
  int64_t n=0;double sum=0;int64_t low=start.empty()?INT64_MIN:stamp(start),high=end.empty()?INT64_MAX:stamp(end);
  std::unique_ptr<parquet::arrow::FileWriter> w;
  if(!subset.empty())w=writer(subset,native_schema());
  if(extract)std::cout<<"source_timestamp_naive,exported_value,source_csv_record_1_based,canonical_vwc_percent\n"<<std::setprecision(17);
  while(auto b=take(batches->Next())) {
    n+=b->num_rows();int vi=b->schema()->GetFieldIndex("exported_value");
    if(vi<0)continue;
    auto v=std::static_pointer_cast<arrow::DoubleArray>(b->column(vi));
    auto ts=std::static_pointer_cast<arrow::TimestampArray>(b->GetColumnByName("source_timestamp_naive"));
    auto rows=std::static_pointer_cast<arrow::Int64Array>(b->GetColumnByName("source_csv_record_1_based"));
    auto cv=std::static_pointer_cast<arrow::DoubleArray>(b->GetColumnByName("canonical_vwc_percent"));
    if(w) {
      int64_t first=0,last=b->num_rows();
      while(first<last&&ts->Value(first)/1000000<low)first++;
      while(last>first&&ts->Value(last-1)/1000000>=high)last--;
      if(last>first){auto part=b->Slice(first,last-first);check(w->WriteTable(*arrow::Table::Make(part->schema(),part->columns()),65536));}
    }
    for(int64_t i=0;i<b->num_rows();i++) {
      if(!std::isfinite(v->Value(i))||!std::isfinite(cv->Value(i)))throw std::runtime_error("Nonfinite readback");
      sum+=v->Value(i);
      if(extract&&ts->Value(i)/1000000>=low&&ts->Value(i)/1000000<high) {
        time_t t=ts->Value(i)/1000000;char text[32];std::tm tm{};gmtime_r(&t,&tm);strftime(text,sizeof(text),"%Y-%m-%d %H:%M:%S",&tm);
        std::cout<<text<<','<<v->Value(i)<<','<<rows->Value(i)<<','<<cv->Value(i)<<'\n';
      }
    }
  }
  if(w)check(w->Close());
  if(!extract)std::cout<<"{\"rows\":"<<n<<",\"finite_exported_sum\":"<<std::setprecision(17)<<sum<<"}\n";
}
int main(int argc,char** argv) {
  try {
    if(argc==6&&std::string(argv[1])=="native")native(argv[2],argv[3],argv[4],std::stoi(argv[5]));
    else if(argc==5&&std::string(argv[1])=="table")table(argv[2],argv[3],argv[4]);
    else if(argc==3&&std::string(argv[1])=="readback")readback(argv[2],false,"","");
    else if(argc==5&&std::string(argv[1])=="extract")readback(argv[2],true,argv[3],argv[4]);
    else if(argc==6&&std::string(argv[1])=="subset")readback(argv[2],false,argv[3],argv[4],argv[5]);
    else throw std::runtime_error("Usage: native INPUT CONTROL OUTPUT BLOCK | table CSV PARQUET TYPES | readback PARQUET | extract PARQUET START END | subset PARQUET START END OUTPUT");
    return 0;
  }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 2;}
}
