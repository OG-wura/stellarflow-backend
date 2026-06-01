declare module 'csv-writer' {
  export interface CsvWriterHeader {
    id: string;
    title: string;
  }
  export interface ObjectCsvWriterParams {
    path: string;
    header: CsvWriterHeader[] | string[] | any;
    append?: boolean;
    encoding?: string;
    alwaysQuote?: boolean;
    fieldDelimiter?: string;
    recordDelimiter?: string;
  }
  export interface CsvWriter {
    writeRecords(records: any[]): Promise<void>;
  }
  export function createObjectCsvWriter(params: ObjectCsvWriterParams): CsvWriter;
}
