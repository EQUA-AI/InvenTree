import { Component, type ReactNode, Suspense, lazy } from 'react';
import type { ChatMessage } from '../../../hooks/UseAIChat';

const OpenUIMessage = lazy(() => import('./OpenUIMessage'));
class PresentationBoundary extends Component<
  { fallback: ReactNode; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}
export interface RichMessageProps {
  message: ChatMessage;
  narrative: ReactNode;
  facts: ReactNode;
  qualifications: ReactNode;
}
/** The shell and ordinary prose never load the parser/runtime bundle. */
export function RichMessage(props: RichMessageProps) {
  const fallback = (
    <>
      {props.narrative}
      {props.facts}
      {props.qualifications}
    </>
  );
  if (
    !props.message.presentation ||
    props.message.isStreaming ||
    import.meta.env.VITE_CHAT_PRESENTATION === 'false'
  )
    return fallback;
  return (
    <PresentationBoundary
      key={`${props.message.id}:${props.message.presentation.program}`}
      fallback={fallback}
    >
      <Suspense fallback={fallback}>
        <OpenUIMessage {...props} fallback={fallback} />
      </Suspense>
    </PresentationBoundary>
  );
}
