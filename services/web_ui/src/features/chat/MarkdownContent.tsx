import React, { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Copy, Check } from "lucide-react";
import { cleanLatexArrows } from "../../lib/textCleanup";
import styles from "./MarkdownContent.module.css";

interface MarkdownContentProps {
  content: string;
}

export const MarkdownContent: React.FC<MarkdownContentProps> = ({ content }) => {
  const cleaned = cleanLatexArrows(content);

  return (
    <div className={styles.markdown}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ ...props }) => (
            <a {...props} target="_blank" rel="noopener noreferrer" />
          ),
          code: ({ className, children, ...props }) => {
            const match = /language-(\w+)/.exec(className || "");
            const language = match ? match[1] : "";
            const isInline = !className;
            const textContent = String(children).replace(/\n$/, "");

            if (isInline) {
              return (
                <code className={styles.inlineCode} {...props}>
                  {children}
                </code>
              );
            }

            return (
              <CodeBlockWithCopy language={language} code={textContent}>
                <code className={className} {...props}>
                  {children}
                </code>
              </CodeBlockWithCopy>
            );
          },
        }}
      >
        {cleaned}
      </ReactMarkdown>
    </div>
  );
};

interface CodeBlockWithCopyProps {
  language: string;
  code: string;
  children: React.ReactNode;
}

const CodeBlockWithCopy: React.FC<CodeBlockWithCopyProps> = ({
  language,
  code,
  children,
}) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Ignore clipboard write failures
    }
  };

  return (
    <div className={styles.codeBlockWrapper}>
      <div className={styles.codeHeader}>
        <span>{language || "code"}</span>
        <button
          className={styles.copyCodeBtn}
          onClick={handleCopy}
          aria-label="Copy code block"
        >
          {copied ? (
            <>
              <Check size={12} color="var(--badge-green-text)" />
              <span>Copied!</span>
            </>
          ) : (
            <>
              <Copy size={12} />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>
      <pre className={styles.codeBlock}>{children}</pre>
    </div>
  );
};
