(define (problem rebuild-02)
  (:domain blocks-world)
  (:objects a b c d e f - block)
  (:init
    (clear b) (hand-empty) (on a e) (on b a) (on d c) (on e f) (on f d) (on-table c))
  (:goal (and (on b e) (on d c) (on-table a) (on-table c) (on-table e) (on-table f)))
)
