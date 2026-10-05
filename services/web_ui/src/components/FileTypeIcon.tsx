import React from "react";
import {
  FileText,
  FileType,
  Presentation,
  FileSpreadsheet,
  FileCode,
  File,
} from "lucide-react";

interface FileTypeIconProps {
  type: string;
  size?: number;
  className?: string;
}

export const FileTypeIcon: React.FC<FileTypeIconProps> = ({
  type,
  size = 18,
  className = "",
}) => {
  const normalized = type.toLowerCase().replace(/^\./, "");

  switch (normalized) {
    case "pdf":
      return <FileText size={size} color="#ea4335" className={className} />;
    case "doc":
    case "docx":
      return <FileType size={size} color="#4285f4" className={className} />;
    case "ppt":
    case "pptx":
      return <Presentation size={size} color="#fa7b17" className={className} />;
    case "xls":
    case "xlsx":
    case "csv":
      return <FileSpreadsheet size={size} color="#34a853" className={className} />;
    case "md":
    case "markdown":
      return <FileCode size={size} color="#a142f4" className={className} />;
    case "txt":
    default:
      return <File size={size} color="#9aa0a6" className={className} />;
  }
};
