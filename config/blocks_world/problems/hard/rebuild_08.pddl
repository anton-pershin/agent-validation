(define (problem rebuild-08)
  (:domain blocks-world)
  (:objects a b c d e f - block)
  (:init
    (clear c) (clear e) (clear f) (hand-empty) (on a b) (on b d) (on e a) (on-table c) (on-table d) (on-table f))
  (:goal (and (on b c) (on e b) (on f d) (on-table a) (on-table c) (on-table d)))
)
